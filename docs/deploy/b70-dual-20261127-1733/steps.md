[17:33:17] step 1 ok: environment in docs/deploy/b70-dual-20261127-1733/environment.txt, image vllm/vllm-openai-xpu@sha256:e2ab50957fc309d3fb110e6331ab8bfb7dd4f0074df41cbbf0bc3c64c8bc0e52
[17:33:17] starting vllm-asr: openai/whisper-large-v3-turbo as whisper on card 1, :8001, util 0.12, args: --max-model-len 448
[17:35:21]   vllm-asr answering on :8001 after 120 s
[17:35:21] starting vllm-qwen3-asr: Qwen/Qwen3-ASR-1.7B as qwen3-asr on card 1, :8002, util 0.3, args: --max-model-len 8192
[17:37:26]   vllm-qwen3-asr answering on :8002 after 120 s
[17:37:26] starting vllm-haven: Qwen/Qwen3-32B-AWQ as qwen3-32b-awq on card 0, :8000, util 0.9, args: --max-model-len 32768 --kv-cache-dtype fp8
[17:39:30]   vllm-haven answering on :8000 after 120 s
[17:39:51] step 4: requests in docs/deploy/b70-dual-20261127-1733/requests.txt
[17:39:51] step 5: card memory recorded
[17:39:52] summary: docs/deploy/b70-dual-20261127-1733.md (logs in docs/deploy/b70-dual-20261127-1733/)
