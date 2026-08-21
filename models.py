from pydantic import BaseModel
from typing import List, Dict, Any, Optional


class CandidateInfo(BaseModel):
    # Sent by the browser, but NOT trusted: the server re-resolves the identity
    # and overwrites name and plant from the employee master. A form field is
    # not evidence of who somebody is.
    candidate_name: Optional[str] = None
    plant_location: Optional[str] = None
    assessment_date: str
    access_code: Optional[str] = None  # 10-digit HR-shared code; required for public flow

    # Which door the candidate came through.
    #
    #   "employee" — on-roll or off-roll. employee_code AND email are both
    #                required, and both are checked against the master.
    #   "external" — a recruitment candidate who is not on the rolls yet.
    #                E-mail identifies them and the name is typed, because
    #                there is nothing to look it up in.
    #
    # Not enforced by the type: the fields a flow requires differ, so
    # start_session validates per type and can say which field is missing
    # instead of pydantic rejecting the whole body with one shape.
    candidate_type: str = "employee"
    employee_code: Optional[str] = None
    email: str


class IdentityLookup(BaseModel):
    employee_code: str
    email: str


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


class ValidationBatchRequest(BaseModel):
    # Force-full re-baseline selection. All optional:
    #   names        → match by candidate name (case-insensitive)
    #   session_ids  → match by session id
    #   neither      → ALL completed sessions with stored answers
    session_ids: Optional[List[str]] = None
    names:       Optional[List[str]] = None


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
