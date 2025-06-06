import asyncio
import logging
import os
import time
import uuid

import numpy as np
from aiohttp import web
import aiohttp_cors
from baseline_pipeline import pipe, block_size
from scipy import signal

logging.basicConfig(level=logging.DEBUG)


class AudioProcessor:
    def __init__(self):
        self.buffer = np.empty((0, 1), dtype=np.float32)
        self.silence_duration = 0
        self.is_speaking = False
        self.last_sent_text = None
        self.min_speech_duration = 0.5
        self.silence_threshold = 0.01
        self.speech_started_at = None


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    processor = AudioProcessor()
    logging.info('WebSocket connection opened')

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                try:
                    if len(msg.data) <= 2:
                        continue
                        
                    audio_chunk = np.frombuffer(msg.data, dtype=np.int16)
                    
                    if len(audio_chunk) == 0:
                        continue
                        
                    float_data = audio_chunk.astype(np.float32) / 32768.0
                    
                    if len(float_data) <= 3:
                        continue
                        
                    resampled_data = signal.resample(float_data, len(float_data) // 3)
                    current_amplitude = np.max(np.abs(resampled_data))

                    is_current_chunk_speech = current_amplitude > processor.silence_threshold

                    if is_current_chunk_speech:
                        if not processor.is_speaking:
                            processor.is_speaking = True
                            processor.speech_started_at = time.time()
                            logging.info("Speech started")
                        processor.silence_duration = 0
                    else:
                        processor.silence_duration += len(resampled_data) / 16000

                    if resampled_data.size > 0:
                        processor.buffer = np.concatenate([
                            processor.buffer,
                            resampled_data.reshape(-1, 1)
                        ])

                    should_process = (
                        processor.is_speaking and
                        processor.silence_duration > 0.3 and
                        (time.time() - processor.speech_started_at) > processor.min_speech_duration
                    )

                    if should_process:
                        logging.info(f"Processing speech chunk of length {processor.buffer.shape[0]}")
                        chunk = processor.buffer.flatten()
                        processor.buffer = np.empty((0, 1), dtype=np.float32)
                        processor.is_speaking = False

                        try:
                            result = pipe(chunk, generate_kwargs={
                                "language": "ru",
                                "task": "transcribe"
                            })["text"].strip()

                            if result and result not in ["Продолжение следует...", "Спасибо."] and result != processor.last_sent_text:
                                logging.info(f"Sending result: {result}")
                                await ws.send_str(result)
                                processor.last_sent_text = result

                        except Exception as e:
                            logging.error(f"ASR Error: {e}")

                    elif processor.silence_duration > 1.0:
                        processor.buffer = np.empty((0, 1), dtype=np.float32)
                        processor.is_speaking = False

                except Exception as e:
                    logging.error(f"Error processing audio chunk: {e}")

    except Exception as e:
        logging.error(f"Error in websocket handler: {e}")
    finally:
        logging.info('WebSocket connection closed')

    return ws


app = web.Application()
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*"
    )
})

app.router.add_get('/ws', websocket_handler)

for route in list(app.router.routes()):
    cors.add(route)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8080)
