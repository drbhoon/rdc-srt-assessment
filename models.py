from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class CandidateInfo(BaseModel):
    candidate_name: str
    plant_location: str
    assessment_date: str
    access_code: Optional[str] = None  # 10-digit HR-shared code; required for public flow


class AccessCodeValidate(BaseModel):
    code: str


class AccessCodeGenerate(BaseModel):
    label: Optional[str] = None
    max_uses: Optional[int] = 10


class ScoreRequest(BaseModel):
    session_id: str
    srt_id: str
    situation: str
    primary_competency: str
    secondary_competency: str
    candidate_transcript: str


class FinalReportRequest(BaseModel):
    session_id: str


class SubmitAllRequest(BaseModel):
    session_id: str
    answers: Dict[str, str]   # { srt_id: transcript }


class QuestionOut(BaseModel):
    question_number: int
    srt_id: str
    primary_competency: str
    secondary_competency: str
    situation: str


class StartSessionResponse(BaseModel):
    session_id: str
    questions: List[QuestionOut]
    total_questions: int
