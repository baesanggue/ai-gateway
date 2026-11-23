"""시험용 작업 프로세스 (#52): 글자 뽑기에서 메모리를 4GB까지 잡으려 한다 → 메모리 상한에서 MemoryError."""
import sys

from gateway.files import worker

sys.exit(worker.main(lambda filename, data: [bytearray(64 * 1024 * 1024) for _ in range(64)]))
