import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://mineru.net/api/v4"
POLL_INTERVAL = 3
MAX_WAIT = 600


class MinerUService:
    """MinerU 云端 API：先上传文件获取预签名 URL，PUT 上传后自动解析。"""

    def __init__(self):
        self.token = os.getenv("MINERU_API_KEY", "")
        self.model_version = os.getenv("MINERU_MODEL_VERSION", "vlm")
        if not self.token:
            raise RuntimeError("MINERU_API_KEY is required. Set it in .env (apply at https://mineru.net)")

    @property
    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = requests.request(
            method, f"{API_BASE}{path}",
            headers=self._headers,
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code")
        if code and code != 0:
            raise RuntimeError(f"MinerU API error code={code}: {data.get('msg', data)}")
        return data

    # ── file upload flow ──────────────────────────────────────────

    def _upload_file(self, filepath: str, filename: str,
                     is_ocr: bool = True) -> str:
        """Step 1+2: 申请上传链接 → PUT 文件 → 返回 batch_id。"""
        # Step 1 — 申请预签名上传 URL
        data = self._request("POST", "/file-urls/batch", json={
            "files": [{"name": filename, "is_ocr": is_ocr}],
            "model_version": self.model_version,
        })
        batch_id = data["data"]["batch_id"]
        upload_urls = data["data"]["file_urls"]
        if not upload_urls:
            raise RuntimeError(f"No upload URLs returned: {data}")

        # Step 2 — PUT 文件到预签名 URL
        with open(filepath, "rb") as f:
            put_resp = requests.put(upload_urls[0], data=f, timeout=120)
        put_resp.raise_for_status()

        return batch_id

    def _poll_batch(self, batch_id: str) -> str:
        """Step 3: 轮询批量任务，完成后返回 markdown 文本。"""
        start = time.time()
        while time.time() - start < MAX_WAIT:
            data = self._request("GET", f"/extract/task/batch/{batch_id}")
            batch = data.get("data", {})

            state = batch.get("state") or batch.get("status") or ""
            if state in ("done", "completed", "success"):
                files = batch.get("files", [])
                if not files:
                    raise RuntimeError("Batch completed but no files in response.")

                file_info = files[0]
                task_id = file_info.get("task_id")
                if task_id:
                    return self._poll_task(task_id)

                # Some APIs return content inline
                md = file_info.get("md_content") or file_info.get("content")
                if md:
                    return md
                raise RuntimeError(f"Cannot extract content from batch result: {file_info}")

            if state in ("failed", "error", "cancelled"):
                raise RuntimeError(f"Batch {batch_id} failed: {batch}")

            time.sleep(POLL_INTERVAL)

        raise TimeoutError(f"Batch {batch_id} timed out after {MAX_WAIT}s")

    # ── single task poll (used after batch, or for URL-based tasks) ─

    def _poll_task(self, task_id: str) -> str:
        """轮询单个任务直到完成，返回 markdown。"""
        start = time.time()
        while time.time() - start < MAX_WAIT:
            data = self._request("GET", f"/extract/task/{task_id}")
            task = data.get("data", {})

            state = task.get("state") or task.get("status") or ""
            if state in ("done", "completed", "success"):
                # The response may include md_content directly or a result URL
                md = task.get("md_content") or task.get("content") or task.get("markdown")
                if md:
                    return md
                # Fallback: result might be a downloadable zip/md URL
                result_url = task.get("result_url") or task.get("url")
                if result_url:
                    resp = requests.get(result_url, timeout=60)
                    resp.raise_for_status()
                    return resp.text
                raise RuntimeError(f"Task completed but no content: {task}")

            if state in ("failed", "error", "cancelled"):
                raise RuntimeError(f"Task {task_id} failed: {task}")

            time.sleep(POLL_INTERVAL)

        raise TimeoutError(f"Task {task_id} timed out after {MAX_WAIT}s")

    # ── public API ─────────────────────────────────────────────────

    def parse(self, filepath: str, filename: str, ocr: bool = True) -> str:
        """解析本地 PDF 文件：上传 → 轮询 → 返回 markdown。"""
        batch_id = self._upload_file(filepath, filename, is_ocr=ocr)
        return self._poll_batch(batch_id)

    def parse_url(self, url: str, ocr: bool = True) -> str:
        """解析远程文件 URL：提交任务 → 轮询 → 返回 markdown。"""
        data = self._request("POST", "/extract/task", json={
            "url": url,
            "is_ocr": ocr,
            "model_version": self.model_version,
        })
        task_id = data["data"]["task_id"]
        return self._poll_task(task_id)
