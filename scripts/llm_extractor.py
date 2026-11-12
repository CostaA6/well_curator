"""
Módulo de inferencia local con llama.cpp (llama-server.exe) sobre GPU NVIDIA RTX 4060
usando el modelo local gemma-4-E4B-it-Q4_K_M.gguf (Spec 001 - RF-6, RNF-2).
El agente recibe directamente el texto crudo de los encabezados .LAS (cortado antes de ~A)
y regulariza Nombre, UWI, X, Y, Cota y las curvas detectadas en ~Curve.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVER_BIN = PROJECT_ROOT / "model" / "bin" / "llama-server.exe"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "model" / "gemma-4-E4B-it-Q4_K_M.gguf"

METADATA_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "nombre_pozo": {"type": "string"},
        "uwi": {"type": ["string", "null"]},
        "coord_x": {"type": ["number", "null"]},
        "coord_y": {"type": ["number", "null"]},
        "cota": {"type": ["number", "null"]},
        "referencia_cota": {"type": ["string", "null"]},
        "fuente_coordenadas": {"type": "string"},
        "curvas_detectadas": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "nombre_pozo",
        "uwi",
        "coord_x",
        "coord_y",
        "cota",
        "referencia_cota",
        "fuente_coordenadas",
        "curvas_detectadas",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Eres un experto en petrofísica y curación de archivos .LAS de perfilaje de pozos.
Recibirás el bloque con los encabezados CRUDOS originales (todo lo previo a ~A: secciones ~Version, ~Well, ~Curve, ~Parameter) de los archivos .LAS de un mismo pozo.
Tu tarea es leer esos encabezados y devolver ÚNICAMENTE un objeto JSON regularizado con los metadatos y las curvas detectadas.

Reglas críticas de dominio:
1. nombre_pozo: Nombre oficial normalizado del pozo (ej. "YPF.SC.CVa-19", "YPF.SC.CVa-24", "YPF.SC.CVa-38", sin puntos espurios antes del guion).
2. uwi: Identificador único oficial del pozo SIEMPRE EN MAYÚSCULAS y de 14 caracteres (busca en UWI, UWID o API; si un archivo tiene el UWI vacío, truncado como "AR010000" o con un código interno como "SAI-369", elige el UWI oficial completo tipo "AR010000837200" o "AR010000701100").
3. coord_x y coord_y: Coordenadas planas Gauss-Krüger (Faja 2 - Cuenca del Golfo San Jorge) en formato numérico decimal estándar (punto como separador decimal, sin separador de miles):
   - coord_x (Este / XWELL) SIEMPRE está en el orden de ~2481000 (ej. 2.481.797,29 -> 2481797.29).
   - coord_y (Norte / YWELL) SIEMPRE está en el orden de ~4834000 (ej. 4.834.255,30 -> 4834255.30).
   - Nota: Algunos operadores invierten las etiquetas X e Y en el header; asigna siempre ~2481000 a coord_x y ~4834000 a coord_y. Si un archivo preliminar tiene un error de tipeo en las coordenadas, usa el valor consistente del resto de los archivos del pozo.
4. cota: Elevación del terreno o datum permanente en metros como número decimal (busca Z en LOC, FL2, EPD o EGL; ej. 294,14 -> 294.14).
5. referencia_cota: Referencia de la cota (ej. "NIVEL DEL TERRENO" o "N.T." según PDAT/LMF/DMF).
6. fuente_coordenadas: Breve nota indicando de qué campos del header tomaste X, Y y Cota.
7. curvas_detectadas: Lista con los mnemónicos de las curvas presentes en la sección ~Curve de los archivos de PROFUNDIDAD (DEPTH) del pozo (excluyendo DEPT). Si una curva se repite, inclúyela con sufijo ":2", ":3", etc.
"""


def normalize_well_metadata(
    meta: Dict[str, object],
    fallback_well_name: Optional[str] = None,
) -> Dict[str, object]:
    """
    Aplica normalización determinística post-LLM (Spec 002 - RF-1):
    - Limpia puntos espurios en nombre_pozo (ej. YPF.SC.CV.a-38 -> YPF.SC.CVa-38).
    - Normaliza UWI en mayúsculas y completa UWIs AR01 de 12 caracteres a 14 caracteres con '00'.
    - Garantiza que coord_x sea Este (~2.48x.xxx) y coord_y sea Norte (~4.83x.xxx).
    """
    out = dict(meta)

    raw_name = str(out.get("nombre_pozo") or fallback_well_name or "").strip()
    raw_name = re.sub(r"\.a-", "a-", raw_name)
    raw_name = re.sub(r"\.-", "-", raw_name)
    if fallback_well_name and not raw_name:
        raw_name = fallback_well_name
    out["nombre_pozo"] = raw_name

    uwi_val = out.get("uwi")
    if uwi_val:
        uwi_str = re.sub(r"\s+", "", str(uwi_val).upper())
        if re.match(r"^AR\d{10}$", uwi_str):
            uwi_str = uwi_str + "00"
        out["uwi"] = uwi_str

    cx = out.get("coord_x")
    cy = out.get("coord_y")
    if isinstance(cx, (int, float)) and isinstance(cy, (int, float)):
        if float(cx) > 3_500_000 and float(cy) < 3_500_000:
            out["coord_x"], out["coord_y"] = float(cy), float(cx)
        else:
            out["coord_x"], out["coord_y"] = float(cx), float(cy)

    if out.get("cota") is not None:
        out["cota"] = round(float(out["cota"]), 2)

    return out


class LocalLlamaServer:
    """Administra el ciclo de vida de llama-server.exe sobre la GPU RTX 4060."""

    def __init__(
        self,
        server_bin: Path = DEFAULT_SERVER_BIN,
        model_path: Path = DEFAULT_MODEL_PATH,
        port: int = 8089,
        gpu_device: str = "Vulkan1",
    ) -> None:
        self.server_bin = server_bin
        self.model_path = model_path
        self.port = port
        self.gpu_device = gpu_device
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: Optional[subprocess.Popen] = None

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
            "99",
            "-c",
            "16384",
            "-np",
            "1",
            "--port",
            str(self.port),
            "--host",
            "127.0.0.1",
        ]
        logger.info("Iniciando llama-server en GPU (%s): %s", self.gpu_device, " ".join(cmd))
        log_path = PROJECT_ROOT / "tests" / "001-extraccion-y-exploracion-las" / "llama_server.log"
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

    def extract_well_metadata(self, consolidated_header_block: str) -> Dict[str, object]:
        """Envía el bloque de encabezados crudos del pozo a Gemma-4-E4B y retorna el JSON estructurado."""
        payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Lee los siguientes encabezados .LAS crudos del pozo y devuelve el JSON con "
                        "los metadatos regularizados y las curvas detectadas:\n\n"
                        f"{consolidated_header_block}"
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 3072,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "well_metadata",
                    "strict": True,
                    "schema": METADATA_JSON_SCHEMA,
                },
            },
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} de llama-server: {err_body}") from e
        elapsed = round(time.time() - t0, 2)

        msg = body["choices"][0]["message"]
        content_str = (msg.get("content") or "").strip()
        if not content_str and msg.get("reasoning_content"):
            content_str = msg["reasoning_content"].strip()

        start_idx = content_str.find("{")
        end_idx = content_str.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            content_str = content_str[start_idx : end_idx + 1]

        parsed = json.loads(content_str)
        parsed = normalize_well_metadata(parsed)
        parsed["_inference_time_s"] = elapsed
        parsed["_model"] = self.model_path.name
        parsed["_gpu_device"] = self.gpu_device
        return parsed

    def __enter__(self) -> "LocalLlamaServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
