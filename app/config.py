from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = ""
    poll_interval_minutes: int = 10
    min_poll_interval_minutes: int = 5
    max_filters_per_user: int = 10
    request_delay_seconds: float = 3.0
    parser_max_pages: int = 2
    parse_failure_threshold: int = 5
    max_notify_per_filter: int = 15
    database_url: str = "sqlite+aiosqlite:///./data/bidcarbot.db"
    log_level: str = "INFO"
    log_dir: str = "./data/logs"
    timezone: str = "Europe/Kyiv"
    request_timeout_seconds: float = 30.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    usdt_trc20_wallet: str = "TKXbcn4tpzudTc66CP65prmE6rcgmTybAs"
    sub_month_usdt: str = "5"
    sub_year_usdt: str = "50"
    god_mode_password: str = "defaultpass"
    trongrid_api_key: str = ""
    trongrid_base_url: str = "https://api.trongrid.io"

    @property
    def effective_poll_interval(self) -> int:
        return max(self.poll_interval_minutes, self.min_poll_interval_minutes)


settings = Settings()
