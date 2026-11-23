"""시험용 작업 프로세스 (#52): 메모리 상한을 건 뒤 글자 뽑기에서 멈춘다 → 게이트웨이가 시간 상한으로 끈다."""
import sys
import time

from gateway.files import worker

sys.exit(worker.main(lambda filename, data: time.sleep(120)))
