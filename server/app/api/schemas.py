"""接口层通用:请求体模型 + 统一响应包装。"""
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..utils import fail, ok


class LoginReq(BaseModel):
    username: str = ""
    password: str = ""


class CredentialReq(BaseModel):
    """改管理员账密(仅管理员)。密码不回传, 只写库摘要。"""
    username: str = ""
    password: str = ""


class SettingReq(BaseModel):
    key: str
    value: Any


class SeedReq(BaseModel):
    import_index: bool = False
    import_history: bool = False
    seed_dir: Optional[str] = None


class DetailReq(BaseModel):
    limit: int = 100
    ids: Optional[list[int]] = None
    only_missing: bool = True
    concurrency: int = 6
    with_readme: bool = False
    with_contributors: bool = True
    with_release: bool = True


class ReadmeReq(BaseModel):
    limit: int = 200
    concurrency: int = 6
    refresh_after_days: int = 30


class HistReq(BaseModel):
    limit: int = 200
    concurrency: int = 3
    refresh_after_days: int = 7
    only_missing: bool = True
    ids: Optional[list[int]] = None


class SnapshotReq(BaseModel):
    stat_date: Optional[str] = None


class RefreshReq(BaseModel):
    star_limit: int = 1000
    full_history: bool = False
    harvest: bool = True
    min_stars: int = 50
    max_age_days: int = 1095
    backfill: bool = True
    ai_group: int = 15
    ai_concurrency: int = 10


class BatchReq(BaseModel):
    ids: Optional[list[int]] = None
    names: Optional[list[str]] = None
    page_size: Optional[int] = None


class WhitelistReq(BaseModel):
    full_name: str
    list_type: str = "major"
    note: Optional[str] = None


class WhitelistDelReq(BaseModel):
    ids: list[int] = Field(default_factory=list)


class ComputeReq(BaseModel):
    period_type: str = "week"
    metric: str = "total"
    period_key: Optional[str] = None
    force: bool = False
    min_stars: Optional[int] = None
    exclude_major: bool = False


class EnsureReq(BaseModel):
    period_type: str = "week"
    metric: str = "gain"
    period_key: Optional[str] = None
    min_stars: Optional[int] = None
    exclude_major: bool = False
    collect: bool = True
    max_collect: int = 20


class TaskRunReq(BaseModel):
    task_key: str
    model_id: Optional[int] = None
    limit: int = 20
    concurrency: int = 4
    group_size: int = 10
    ids: Optional[list[int]] = None
    allow_overwrite: bool = False


class ProviderReq(BaseModel):
    id: Optional[int] = None          # 已保存的供应商: 拉取/测试时用它回落密钥
    name: Optional[str] = None
    # 协议暂定仅支持 OpenAI Response(/v1/responses)
    provider_type: str = "openai_response"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_prefix: str = "/v1"
    headers: Optional[dict] = None
    timeout_s: int = 120
    max_retries: int = 2
    enabled: bool = True
    is_default: bool = False
    remark: Optional[str] = None
    models: Optional[list] = None
    model_names: Optional[list[str]] = None


class ModelReq(BaseModel):
    provider_id: int
    model_name: str
    display_name: Optional[str] = None
    model_type: str = "chat"
    max_tokens: int = 4096
    temperature: float = 0.3
    enabled: bool = True
    is_default: bool = False


class TaskReq(BaseModel):
    id: Optional[int] = None
    task_key: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    task_type: str = "summary"
    system_prompt: Optional[str] = None
    prompt_template: Optional[str] = None
    model_id: Optional[int] = None
    concurrency: int = 3
    batch_size: int = 20
    max_readme_len: Optional[int] = None
    enabled: Optional[bool] = None


class WatchAddReq(BaseModel):
    full_name: str
    watch_type: str = "any"
    threshold: int = 50
    interval_min: int = 60
    webhook_url: Optional[str] = None
    note: Optional[str] = None
    channels: Optional[list] = None


class WatchUpdateReq(BaseModel):
    enabled: Optional[bool] = None
    threshold: Optional[int] = None
    interval_min: Optional[int] = None
    watch_type: Optional[str] = None
    webhook_url: Optional[str] = None
    note: Optional[str] = None


class ReadReq(BaseModel):
    all: bool = False
    ids: Optional[list[int]] = None


def wrap(fn, *args, **kwargs):
    """执行服务函数并包装为统一响应体;异常转 code=1。"""
    try:
        return ok(fn(*args, **kwargs))
    except Exception as e:  # noqa: BLE001
        return fail(str(e))
