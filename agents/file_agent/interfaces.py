from dataclasses import dataclass, field
from typing import Literal, Any

type FileOperationStatus = Literal["success", "error", "permission_denied"]

@dataclass
class FileOperationResult:
    """
    파일 작업 결과를 나타내는 데이터 클래스입니다.

    Attributes:
        status (FileOperationStatus): 작업 상태.
        message (str): 상세 결과 메시지.
        data (Any | None): 읽기 작업 시 반환되는 데이터 등 추가 정보.
    """
    status: FileOperationStatus
    message: str
    data: Any | None = field(default=None)
