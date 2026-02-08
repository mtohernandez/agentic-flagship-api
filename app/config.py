from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"
    groq_temperature: float = 0.0

    agent_recursion_limit: int = 15
    agent_request_timeout: int = 120
    agent_max_retries: int = 2

    groq_max_tokens: int = 2048

    browser_enabled: bool = True
    browser_headless: bool = True
    browser_nav_timeout: int = 60000
    browser_action_timeout: int = 10000

    cors_origins: str = "*"

    api_keys: str
    rate_limit_rpm: int = 20

    debug: bool = False

    @model_validator(mode="after")
    def _parse(self):
        self._api_keys_list = [k.strip() for k in self.api_keys.split(",") if k.strip()]
        self._cors_origins_list = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return self

    def get_api_keys(self) -> list[str]:
        return self._api_keys_list

    def get_cors_origins(self) -> list[str]:
        return self._cors_origins_list
