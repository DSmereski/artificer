@echo off
REM Start Ollama with hive-tuned env vars: parallel slots per model,
REM 24h keep-alive (no cold-load between requests), 2 models resident.
REM Kill any running Ollama first.

taskkill /F /IM ollama.exe 2>nul
timeout /t 2 /nobreak >nul

REM NUM_PARALLEL=2 was ALSO overflowing GPU (11.5GB total with KV
REM cache for 2x 8192-context slots > the 12GB usable on a 16GB GPU
REM after Windows DWM/etc), forcing planner-qwen onto CPU entirely
REM (size_vram: 0 in /api/ps). Dropping to 1 keeps the model on GPU.
REM Concurrent helpers serialise — that's the trade-off — but a
REM 5-second helper is better than a 90-second timed-out one.
set "OLLAMA_NUM_PARALLEL=1"
set "OLLAMA_KEEP_ALIVE=24h"
set "OLLAMA_MAX_LOADED_MODELS=2"
REM GPU 0 = gaming 4080 (reserved for Star Citizen).
REM GPU 2 = reserved for Terry voice bot (~7 GB PyTorch resident).
REM Sharing GPU2 with Ollama caused mid-run VRAM eviction → CPU drift.
REM Pin Ollama to GPU 1 alone — planner-qwen is 11 GB, fits in 16 GB.
set "CUDA_VISIBLE_DEVICES=1"

start "Ollama" /MIN "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
echo Ollama starting with NUM_PARALLEL=%OLLAMA_NUM_PARALLEL% KEEP_ALIVE=%OLLAMA_KEEP_ALIVE% MAX_LOADED=%OLLAMA_MAX_LOADED_MODELS% CUDA=%CUDA_VISIBLE_DEVICES%
