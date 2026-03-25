from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from src.infrastructure.paths import ENV_FILE

class Settings(BaseSettings):
    environment: str = "development"
    
    database_url: str
    redis_url: str
    
    admin_username: str
    admin_password: str
    
    google_api_key: SecretStr
    evolution_api_url: str
    evolution_api_token: SecretStr

    model_config = SettingsConfigDict(
        # Usamos o caminho absoluto cross-platform definido no paths.py
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

# Instância global
config = Settings()