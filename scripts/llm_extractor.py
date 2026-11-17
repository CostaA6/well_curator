"""
Módulo de inferencia agnóstico al modelo LLM (Spec 003 - RF-1, RF-2, RF-3):
- Soporta 3 proveedores intercambiables vía `LLMConfig`:
  1. `llama_cpp_local`: gestiona `llama-server.exe` con cualquier modelo `.gguf` local (ej. Gemma-4-E4B en RTX 4060).
  2. `openai_compatible_local`: se conecta a Ollama / LM Studio / vLLM / llama-server ya activo vía `base_url`.
  3. `cloud_api`: se conecta a APIs externas OpenAI-compatible (OpenAI, Gemini OpenAI endpoint, Groq, OpenRouter) vía `base_url` + `api_key`.
- Implementa fallback automático de salida estructurada (`json_schema` -> `json_object` -> prompt) y reintento de validación.
- Extrae tanto coordenadas planas (`coord_x`, `coord_y`) como geográficas (`latitud`, `longitud`) para priorizar Gauss-Krüger o proyectar con `pyproj`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from scripts.field_profiles import FieldProfile, normalize_well_metadata

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVER_BIN = PROJECT_ROOT / "model" / "bin" / "llama-server.exe"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "model" / "gemma-4-E4B-it-Q4_K_M.gguf"
DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "llm_server.log"

METADATA_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "nombre_pozo": {"type": "string"},
        "uwi": {"type": ["string", "null"]},
        "coord_x": {"type": ["number", "null"]},
        "coord_y": {"type": ["number", "null"]},
        "latitud": {"type": ["number", "null"]},
        "longitud": {"type": ["number", "null"]},
        "sistema_coordenadas_origen": {"type": "string"},
        "cota": {"type": ["number", "null"]},
        "referencia_cota": {"type": ["string", "null"]},
        "fuente_coordenadas": {"type": "string"},
        "curvas_detectadas": {
            "type": "array",
            "items": {"type": "string"},
        },
        "resumen_normalizacion": {"type": "string"},
    },
    "required": [
        "nombre_pozo",
        "uwi",
        "coord_x",
        "coord_y",
        "latitud",
        "longitud",
        "sistema_coordenadas_origen",
        "cota",
        "referencia_cota",
        "fuente_coordenadas",
        "curvas_detectadas",
        "resumen_normalizacion",
    ],
    "additionalProperties": False,
}

BASE_SYSTEM_PROMPT = """Eres un experto en petrofísica y curación de archivos .LAS de perfilaje de pozos.
Recibirás el bloque con los encabezados CRUDOS originales (todo lo previo a ~A: secciones ~Version, ~Well, ~Curve, ~Parameter) de los archivos .LAS de un mismo pozo.
Tu tarea es leer esos encabezados y devolver ÚNICAMENTE un objeto JSON regularizado con los metadatos y las curvas detectadas.

Reglas críticas de extracción:
1. nombre_pozo: Nombre oficial normalizado del pozo tal como surge del encabezado (sin prefijos ni comentarios extra).
2. uwi: Identificador único oficial del pozo (busca en UWI, UWID o API) SIEMPRE EN MAYÚSCULAS y sin espacios internos. Si hay conflictos entre archivos preliminares y definitivos del mismo pozo (ej. UWI vacío, truncado o código interno temporal), elige el identificador oficial más completo.
3. Coordenadas (extracción dual para priorizar Gauss-Krüger y soportar Lat/Lon):
   - coord_x y coord_y: Coordenadas planas proyectadas (Gauss-Krüger o UTM) en formato numérico decimal estándar (punto decimal, sin separador de miles; ej. 2.481.797,29 -> 2481797.29). Respeta X/Este y Y/Norte si aparecen en LOC, FL1, XWELL, YWELL o LONG/LATI cuando contengan valores métricos (>100000). Si no existen coordenadas planas, usa null.
   - latitud y longitud: Coordenadas geográficas en grados decimales (WGS84/POSGAR, ej. -46.6312, -69.2345). Si aparecen en formato Grados-Minutos-Segundos (ej. 46d 37' 52.3" S), conviértelas a grados decimales con signo negativo para Sur (S) y Oeste (W). Si no aparecen en el header, usa null.
   - sistema_coordenadas_origen: Indica "GAUSS_KRUGER", "UTM", "LAT_LON", "AMBOS" o "DESCONOCIDO".
4. cota: Elevación del terreno (GL/EPD/EGL) o datum permanente en metros como número decimal (ej. 294,14 -> 294.14). Usa null si no figura.
5. referencia_cota: Referencia de la cota (ej. "NIVEL DEL TERRENO", "N.T.", "GL", "KB") según PDAT/LMF/DMF/EGL.
6. fuente_coordenadas: Breve nota indicando de qué campos del header tomaste las coordenadas y la cota.
7. curvas_detectadas: Lista con los mnemónicos de las curvas presentes en la sección ~Curve de los archivos de PROFUNDIDAD (DEPTH) del pozo (excluyendo la columna índice DEPT/DEPTH). Si una curva se repite, inclúyela con sufijo ":2", ":3", etc.
8. resumen_normalizacion: Informe técnico breve en español (3 a 5 oraciones) explicando qué archivos .LAS se analizaron, qué conflictos o inconsistencias se detectaron (UWI vacío/truncado, códigos internos, coordenadas faltantes o cruzadas, archivos en tiempo XPT), qué campos del encabezado sustentan Nombre/UWI/X/Y/Cota y qué curvas clave o repetidas se identificaron.
"""


def build_system_prompt(field_profile: Optional[FieldProfile] = None) -> str:
    """Construye el system prompt neutro y anexa sugerencias del `FieldProfile` si existen."""
    if field_profile and field_profile.prompt_domain_hints.strip():
        return f"{BASE_SYSTEM_PROMPT}\n{field_profile.prompt_domain_hints.strip()}\n"
    return BASE_SYSTEM_PROMPT


@dataclass
class LLMConfig:
    """Configuración agnóstica para seleccionar el proveedor y modelo LLM."""
    provider: Literal["llama_cpp_local", "openai_compatible_local", "cloud_api"] = "llama_cpp_local"
    model_name: str = "gemma-4-E4B-it-Q4_K_M.gguf"
    base_url: str = "http://127.0.0.1:8089"
    api_key: Optional[str] = None
    api_key_env: str = "LLM_API_KEY"
    # Opciones exclusivas para llama_cpp_local
    server_bin: Path = DEFAULT_SERVER_BIN
    model_path: Path = DEFAULT_MODEL_PATH
    port: int = 8089
    gpu_device: str = "Vulkan1"
    ctx_size: int = 16384
    n_gpu_layers: int = 99
    log_path: Path = DEFAULT_LOG_PATH
    # Opciones de generación HTTP
    temperature: float = 0.0
    max_tokens: int = 3072
    timeout_s: float = 180.0

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


def _extract_json_dict(raw_text: str) -> Dict[str, object]:
    text = raw_text.strip()
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        text = text[start_idx : end_idx + 1]
    return json.loads(text)


def _validate_extracted_payload(data: Dict[str, object]) -> None:
    if not isinstance(data, dict):
        raise ValueError("La respuesta del LLM no es un objeto JSON.")
    if not str(data.get("nombre_pozo") or "").strip():
        raise ValueError("Falta 'nombre_pozo' no vacío en el JSON del LLM.")
    if not isinstance(data.get("curvas_detectadas"), list):
        raise ValueError("'curvas_detectadas' debe ser una lista JSON.")


def _fit_header_block_to_ctx(
    consolidated_header_block: str,
    ctx_size: int,
    max_tokens: int,
) -> str:
    """
    Si un pozo tiene muchísimos .LAS y supera el presupuesto estimado del contexto,
    recorta secciones ~Parameter/~Other redundantes de archivos secundarios manteniendo
    intactos todos los bloques ~Well y ~Curve para evitar HTTP 400 exceed_context_size_error.
    """
    budget_tokens = max(2048, ctx_size - max_tokens - 1400)
    # Estimación conservadora (~2.8 chars/token para texto técnico denso con números)
    max_chars = int(budget_tokens * 2.8)
    if len(consolidated_header_block) <= max_chars:
        return consolidated_header_block

    logger.warning(
        "Bloque de encabezados extenso (%d chars > %d máx. para ctx=%d); compactando ~Parameter secundarios.",
        len(consolidated_header_block),
        max_chars,
        ctx_size,
    )
    out_lines: List[str] = []
    current_section = ""
    file_idx = 0
    for line in consolidated_header_block.splitlines():
        s = line.strip()
        if s.startswith("================"):
            file_idx += 1
            current_section = ""
            out_lines.append(line)
            continue
        if s.startswith("~"):
            current_section = s[1:].lstrip().upper()[:1]
            out_lines.append(line)
            continue
        # En archivos secundarios (>= 2), omitir líneas largas de ~Parameter / ~Other
        if file_idx >= 2 and current_section in ("P", "O"):
            continue
        out_lines.append(line)

    trimmed = "\n".join(out_lines)
    if len(trimmed) > max_chars:
        trimmed = trimmed[:max_chars] + "\n... [TRUNCADO POR LÍMITE DE CONTEXTO]\n"
    return trimmed


class OpenAICompatibleLLMClient:
    """
    Cliente universal para cualquier servidor `/v1/chat/completions`
    (llama.cpp, Ollama, LM Studio, vLLM, OpenAI, Gemini OpenAI endpoint, Groq, etc.).
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def _chat_endpoint(self) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/v1/chat/completions"

    def _post_chat(
        self,
        messages: List[Dict[str, str]],
        response_mode: Literal["json_schema", "json_object", "plain"],
    ) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if response_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "well_metadata",
                    "strict": True,
                    "schema": METADATA_JSON_SCHEMA,
                },
            }
        elif response_mode == "json_object":
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        api_key = self.config.resolve_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(
            self._chat_endpoint(),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def extract_well_metadata(
        self,
        consolidated_header_block: str,
        field_profile: Optional[FieldProfile] = None,
        fallback_well_name: Optional[str] = None,
    ) -> Dict[str, object]:
        """
        Envía el bloque de encabezados crudos del pozo al modelo configurado.
        Incluye fallback automático `json_schema -> json_object -> plain` y reintento si falla el parseo.
        """
        sys_prompt = build_system_prompt(field_profile)
        fitted_block = _fit_header_block_to_ctx(
            consolidated_header_block,
            ctx_size=self.config.ctx_size,
            max_tokens=self.config.max_tokens,
        )
        schema_keys = ", ".join(METADATA_JSON_SCHEMA["properties"].keys())
        user_prompt = (
            "Lee los siguientes encabezados .LAS crudos del pozo y devuelve ÚNICAMENTE un objeto JSON válido "
            f"con estas claves exactas ({schema_keys}):\n\n"
            f"{fitted_block}"
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ]

        t0 = time.time()
        body: Optional[Dict[str, object]] = None
        last_err: Optional[Exception] = None
        used_mode = "json_schema"

        for mode in ("json_schema", "json_object", "plain"):
            try:
                body = self._post_chat(messages, response_mode=mode)  # type: ignore[arg-type]
                used_mode = mode
                break
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                last_err = RuntimeError(f"HTTP {e.code} ({mode}): {err_body}")
                # Si el endpoint rechaza response_format (400/422), degradar al siguiente modo
                if e.code in (400, 422) and mode != "plain":
                    logger.warning("Proveedor LLM rechazó modo '%s' (HTTP %d); probando fallback.", mode, e.code)
                    continue
                raise last_err from e

        if body is None:
            raise last_err or RuntimeError("No se obtuvo respuesta del proveedor LLM.")

        # Parseo + hasta 1 reintento correctivo si el JSON vino malformado en modo fallback
        parsed: Optional[Dict[str, object]] = None
        for attempt in range(2):
            msg = body["choices"][0]["message"]
            content_str = (msg.get("content") or "").strip()
            if not content_str and msg.get("reasoning_content"):
                content_str = str(msg["reasoning_content"]).strip()

            try:
                candidate = _extract_json_dict(content_str)
                _validate_extracted_payload(candidate)
                parsed = candidate
                break
            except Exception as parse_err:
                if attempt == 0:
                    logger.warning("Respuesta JSON inválida en intento 1 (%s); reintentando corrección...", parse_err)
                    retry_msgs = messages + [
                        {"role": "assistant", "content": content_str or "{}"},
                        {
                            "role": "user",
                            "content": (
                                f"Tu respuesta anterior no cumplió el esquema JSON ({parse_err}). "
                                f"Devuelve SOLO un objeto JSON válido con las claves: {schema_keys}."
                            ),
                        },
                    ]
                    body = self._post_chat(retry_msgs, response_mode=used_mode)  # type: ignore[arg-type]
                else:
                    raise RuntimeError(f"El LLM no devolvió un JSON válido tras reintento: {parse_err}") from parse_err

        elapsed = round(time.time() - t0, 2)
        normalized = normalize_well_metadata(
            parsed or {},
            profile=field_profile,
            fallback_well_name=fallback_well_name,
        )
        normalized["_inference_time_s"] = elapsed
        normalized["_model"] = self.config.model_name
        normalized["_provider"] = self.config.provider
        normalized["_response_mode"] = used_mode
        return normalized


class LocalLlamaServer:
    """Administra el ciclo de vida de `llama-server.exe` cuando se usa `provider='llama_cpp_local'`."""

    def __init__(
        self,
        server_bin: Path = DEFAULT_SERVER_BIN,
        model_path: Path = DEFAULT_MODEL_PATH,
        port: int = 8089,
        gpu_device: str = "Vulkan1",
        ctx_size: int = 16384,
        n_gpu_layers: int = 99,
        log_path: Path = DEFAULT_LOG_PATH,
        config: Optional[LLMConfig] = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = LLMConfig(
                provider="llama_cpp_local",
                model_name=model_path.name,
                base_url=f"http://127.0.0.1:{port}",
                server_bin=server_bin,
                model_path=model_path,
                port=port,
                gpu_device=gpu_device,
                ctx_size=ctx_size,
                n_gpu_layers=n_gpu_layers,
                log_path=log_path,
            )
        self.server_bin = self.config.server_bin
        self.model_path = self.config.model_path
        self.port = self.config.port
        self.gpu_device = self.config.gpu_device
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.config.base_url = self.base_url
        self.process: Optional[subprocess.Popen] = None
        self._client = OpenAICompatibleLLMClient(self.config)

    def is_healthy(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def start(self, timeout_s: int = 90) -> None:
        if self.is_healthy():
            logger.info("llama-server ya se encuentra activo en %s", self.base_url)
            return

        if not self.server_bin.exists():
            raise FileNotFoundError(f"No se encontró llama-server.exe en {self.server_bin}")
        if not self.model_path.exists():
            raise FileNotFoundError(f"No se encontró el modelo GGUF en {self.model_path}")

        cmd = [
            str(self.server_bin),
            "-m",
            str(self.model_path),
            "--device",
            self.gpu_device,
            "-ngl",
            str(self.config.n_gpu_layers),
            "-c",
            str(self.config.ctx_size),
            "-np",
            "1",
            "--port",
            str(self.port),
            "--host",
            "127.0.0.1",
        ]
        logger.info("Iniciando llama-server en GPU (%s): %s", self.gpu_device, " ".join(cmd))
        log_path = self.config.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
        )

        start_t = time.time()
        while time.time() - start_t < timeout_s:
            if self.process.poll() is not None:
                self._log_fh.close()
                out = log_path.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"llama-server terminó prematuramente:\n{out}")
            if self.is_healthy():
                logger.info("llama-server listo en %.1f s sobre GPU %s", time.time() - start_t, self.gpu_device)
                return
            time.sleep(1.0)

        self.stop()
        raise TimeoutError(f"llama-server no respondió /health después de {timeout_s}s")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            logger.info("Deteniendo llama-server (PID %s)...", self.process.pid)
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if getattr(self, "_log_fh", None) and not self._log_fh.closed:
            self._log_fh.close()

    def extract_well_metadata(
        self,
        consolidated_header_block: str,
        field_profile: Optional[FieldProfile] = None,
        fallback_well_name: Optional[str] = None,
    ) -> Dict[str, object]:
        meta = self._client.extract_well_metadata(
            consolidated_header_block=consolidated_header_block,
            field_profile=field_profile,
            fallback_well_name=fallback_well_name,
        )
        meta["_gpu_device"] = self.gpu_device
        return meta

    def __enter__(self) -> "LocalLlamaServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class ManagedLLMSession:
    """
    Context manager unificado que recibe un `LLMConfig`:
    - Si `provider == 'llama_cpp_local'`, levanta y detiene `LocalLlamaServer`.
    - Si `provider in ('openai_compatible_local', 'cloud_api')`, usa directo `OpenAICompatibleLLMClient`.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._local_server: Optional[LocalLlamaServer] = None
        self._client: Optional[OpenAICompatibleLLMClient] = None

    def __enter__(self) -> "ManagedLLMSession":
        if self.config.provider == "llama_cpp_local":
            self._local_server = LocalLlamaServer(config=self.config)
            self._local_server.start()
        else:
            self._client = OpenAICompatibleLLMClient(self.config)
        return self

    def extract_well_metadata(
        self,
        consolidated_header_block: str,
        field_profile: Optional[FieldProfile] = None,
        fallback_well_name: Optional[str] = None,
    ) -> Dict[str, object]:
        if self._local_server is not None:
            return self._local_server.extract_well_metadata(
                consolidated_header_block=consolidated_header_block,
                field_profile=field_profile,
                fallback_well_name=fallback_well_name,
            )
        if self._client is not None:
            return self._client.extract_well_metadata(
                consolidated_header_block=consolidated_header_block,
                field_profile=field_profile,
                fallback_well_name=fallback_well_name,
            )
        raise RuntimeError("ManagedLLMSession debe usarse dentro de un bloque 'with'.")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._local_server is not None:
            self._local_server.stop()
            self._local_server = None


def list_local_gguf_models(models_dir: Path = PROJECT_ROOT / "model") -> List[Path]:
    """Lista todos los archivos .gguf disponibles localmente."""
    if not models_dir.exists():
        return []
    return sorted(models_dir.glob("*.gguf"))


def test_llm_connection(config: LLMConfig) -> Tuple[bool, str, List[str]]:
    """
    Verifica conectividad o disponibilidad del proveedor LLM configurado (Spec 004 - RF-3).
    Retorna `(ok, mensaje, lista_modelos_disponibles)`.
    """
    if config.provider == "llama_cpp_local":
        if not config.server_bin.exists():
            return False, f"No se encontró binario llama-server.exe en {config.server_bin}", []
        if not config.model_path.exists():
            return False, f"No se encontró el archivo GGUF en {config.model_path}", []
        ggufs = [p.name for p in list_local_gguf_models(config.model_path.parent)]
        return (
            True,
            f"Listo para inferencia local con {config.model_path.name} en {config.gpu_device}.",
            ggufs,
        )

    base = config.base_url.rstrip("/")
    models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    headers = {"Content-Type": "application/json"}
    api_key = config.resolve_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(models_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [
            str(item.get("id"))
            for item in data.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        return True, f"Conexión exitosa a {models_url} ({len(models)} modelos).", models
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        return False, f"Error HTTP {e.code} al conectar con {models_url}: {err[:200]}", []
    except Exception as exc:
        return False, f"No se pudo conectar con {models_url}: {exc}", []

