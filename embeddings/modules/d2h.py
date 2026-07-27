import numpy as np

from .types import DocumentEmbeddings


class DeferredD2HPipeline:
    def __init__(self, device="cuda", max_pending=64):
        self.device = device
        self.max_pending = max_pending
        self._pending: list = []
        self._overflow: dict = {}

    def submit(self, doc_id, texts, gpu_t, int8_t=None, bin_t=None):
        self._pending.append((doc_id, texts, gpu_t, int8_t, bin_t))
        if len(self._pending) > self.max_pending:
            self._drain_oldest(self.max_pending // 2)

    def _drain_oldest(self, n):
        for doc_id, texts, gpu_t, int8_t, bin_t in self._pending[:n]:
            self._overflow[doc_id] = DocumentEmbeddings(
                doc_id, texts,
                gpu_t.cpu().float().numpy(),
                int8_t.cpu().numpy() if int8_t is not None else None,
                bin_t.cpu().numpy() if bin_t is not None else None,
            )
        self._pending = self._pending[n:]

    def finalize(self) -> dict:
        self._drain_oldest(len(self._pending))
        res = dict(self._overflow)
        self._overflow.clear()
        return res

    def reset(self):
        self._pending.clear()
        self._overflow.clear()
