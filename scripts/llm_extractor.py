"""
Módulo de inferencia local con llama.cpp (llama-server.exe) sobre GPU NVIDIA RTX 4060
usando el modelo local gemma-4-E4B-it-Q4_K_M.gguf (Spec 001 - RF-6, RNF-2).
"""
from __future__ import annotations

import json
import logging
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
    },
    "required": [
        "nombre_pozo",
        "uwi",
        "coord_x",
        "coord_y",
        "cota",
        "referencia_cota",
        "fuente_coordenadas",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Eres un experto en petrofísica y curación de archivos .LAS de perfilaje de pozos.
A partir del bloque consolidado de encabezados (~WELL y ~PARAMETER) de todos los archivos .LAS de un mismo pozo, debes extraer y unificar los metadatos del pozo y responder ÚNICAMENTE con un objeto JSON válido (sin texto adicional antes ni después).

Esquema exacto del JSON a devolver:
{
  "nombre_pozo": "string",
  "uwi": "string o null",
  "coord_x": number o null,
  "coord_y": number o null,
  "cota": number o null,
  "referencia_cota": "string o null",
  "fuente_coordenadas": "string"
}

Reglas críticas de dominio:
1. nombre_pozo: Nombre oficial del pozo (ej. "YPF.SC.CVa-19").
2. uwi: Identificador único del pozo (busca en UWI, UWID o API en cualquiera de los archivos del pozo; si un archivo lo tiene vacío pero otro del mismo pozo sí lo tiene, usa el valor presente).
3. coord_x y coord_y: Coordenadas planas Gauss-Krüger en formato numérico decimal estándar (punto como separador decimal, sin puntos de miles).
   - Atención: en Argentina (Santa Cruz), X suele estar cerca de 4834000 (ej. 4.834.255,30 -> 4834255.30) e Y suele estar cerca de 2481000 (ej. 2.481.797,29 -> 2481797.29).
   - Pueden aparecer juntas en LOC, repartidas entre LOC y FL1 (~PARAMETER), o dentro de LONG (X) y LATI (Y). Respeta la letra explícita "X:" e "Y:" escrita en el valor.
4. cota: Elevación del terreno o datum permanente en metros como número decimal (busca Z en LOC, FL2, EPD o EGL; ej. 294,14 -> 294.14).
5. referencia_cota: Referencia de la cota (ej. "NIVEL DEL TERRENO" o "N.T." según PDAT/LMF/DMF).
6. fuente_coordenadas: Breve nota indicando de qué mnemónicos tomaste X, Y y Cota (ej. "LOC y FL1/EPD").
"""


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
        """Envía el bloque de encabezados del pozo a Gemma-4-E4B y retorna el JSON estructurado."""
        payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Extrae los metadatos consolidados del siguiente pozo a partir de sus encabezados .LAS "
                        "y responde SOLO con el objeto JSON:\n\n"
                        f"{consolidated_header_block}"
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 2048,
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
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} de llama-server: {err_body}") from e
        elapsed = round(time.time() - t0, 2)

        msg = body["choices"][0]["message"]
        content_str = (msg.get("content") or "").strip()
        if not content_str and msg.get("reasoning_content"):
            content_str = msg["reasoning_content"].strip()

        # Extraer el bloque JSON { ... } si viene envuelto en ```json ... ```
        start_idx = content_str.find("{")
        end_idx = content_str.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            content_str = content_str[start_idx : end_idx + 1]

        parsed = json.loads(content_str)
        parsed["_inference_time_s"] = elapsed
        parsed["_model"] = self.model_path.name
        parsed["_gpu_device"] = self.gpu_device
        return parsed

    def __enter__(self) -> "LocalLlamaServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
