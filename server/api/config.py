from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    tg_bot_token: str
    tg_group_id: str
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    secret_key: str = "dev-secret"
    alert_cooldown_min: int = 30
    daily_report_hour: int = 10

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
