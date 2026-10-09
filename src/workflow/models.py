from typing import Literal
from pydantic import BaseModel, Field


class BriefingButton(BaseModel):
    text: str
    callback_data: str = Field(max_length=64)


class BriefingPayload(BaseModel):
    text: str
    rows: tuple[tuple[BriefingButton, ...], ...]
    disable_web_page_preview: bool = True


class TelegramBriefing(BaseModel):
    channel: Literal['telegram'] = 'telegram'
    chat_id: str
    user_id: str
    payload: BriefingPayload | None = None
    payload_sha256: str | None = None
    delivered: bool = False


class WorkflowRun(BaseModel):
    id: str
    mode: Literal['shadow', 'reviewed_trial', 'production'] = 'shadow'
    days: int = Field(default=14, ge=1, le=365)
    topic_count: int = Field(default=5, ge=1, le=5)
    intent: str = ''
    reviewer: str | None = None
    briefing: TelegramBriefing | None = None
    status: str = 'REQUESTED'
    failures: dict = Field(default_factory=dict)
    checkpoints: dict = Field(default_factory=dict)
    selected_rank: int | None = None
    topic_id: str | None = None
    draft_id: str | None = None
    review_root: str | None = None
    delivery: dict | None = None
    approval: dict | None = None
    trial_scope: dict | None = None
    publication: dict | None = None
    publication_target: dict | None = None
    revision_request: dict | None = None
    revisions: dict = Field(default_factory=dict)
    events: list[dict] = Field(default_factory=list)
    usage_refs: list[str] = Field(default_factory=list)


# DraftStore IDs may begin with '-'; keep them usable as CLI positionals.
import argparse
import re


class DraftArgumentParser(argparse.ArgumentParser):
    def _parse_optional(self, arg_string):
        if arg_string not in self._option_string_actions and re.fullmatch(r'-[A-Za-z0-9_-]{15}', arg_string):
            return None
        return super()._parse_optional(arg_string)
