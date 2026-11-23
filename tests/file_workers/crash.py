"""시험용 작업 프로세스 (#52): 결과를 쓰지 않고 갑자기 끝난다(라이브러리가 프로세스를 죽인 경우)."""
import os
import sys

from gateway.files import worker

sys.exit(worker.main(lambda filename, data: os._exit(3)))
