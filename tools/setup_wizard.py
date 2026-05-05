import os
import secrets
import base64
from pathlib import Path

class SetupWizard:
    def __init__(self, env_path: str | Path = ".env"):
        self.env_path = Path(env_path)
        self.config = {}

    def run(self):
        print("==========================================================")
        print(" Cassiopeia 설정 마법사에 오신 것을 환영합니다! (OpenClaw 스타일) ")
        print("==========================================================")
        print("순서대로 질문에 답하여 .env 파일을 생성합니다.\n")

        self.ask_llm_backend()
        self.ask_api_keys()
        self.ask_integrations()
        
        self.generate_secrets()
        self.save_env()
        
        print("\n==========================================================")
        print(f" ✓ 설정이 완료되었습니다! {self.env_path} 파일이 생성되었습니다.")
        print(" ✓ Setup complete! .env file has been generated.")
        print("==========================================================")
        print("\n [다음 단계 / Next Step]")
        print("  Redis가 실행 중이어야 서버를 시작할 수 있습니다.")
        print("  Redis must be running before starting the server.")
        print()
        print("  Docker : docker-compose up -d redis")
        print("  Local  : redis-server")
        print("==========================================================")

    def ask_llm_backend(self):
        print("1. 어떤 LLM 백엔드를 사용하시겠습니까?")
        print("   (gemini / claude / local)")
        while True:
            choice = input("입력 [기본값: gemini]: ").strip().lower()
            if not choice:
                choice = "gemini"
            
            if choice in ("gemini", "claude", "local"):
                self.config["LLM_BACKEND"] = choice
                break
            print("  [!] 잘못된 입력입니다. 'gemini', 'claude', 'local' 중 하나를 입력하세요.")

    def ask_api_keys(self):
        backend = self.config["LLM_BACKEND"]
        print(f"\n2. {backend} 백엔드를 위한 API 키를 입력해주세요.")
        
        if backend == "gemini":
            key = input("GEMINI_API_KEY 입력: ").strip()
            if key:
                self.config["GEMINI_API_KEY"] = key
                self.config["NLU_LLM_MODEL"] = "gemini-2.0-flash"
        elif backend == "claude":
            key = input("ANTHROPIC_API_KEY 입력: ").strip()
            if key:
                self.config["ANTHROPIC_API_KEY"] = key
        elif backend == "local":
            url = input("LOCAL_LLM_BASE_URL 입력 [기본값: http://localhost:11434/v1]: ").strip()
            self.config["LOCAL_LLM_BASE_URL"] = url if url else "http://localhost:11434/v1"
            model = input("LOCAL_LLM_MODEL 입력 [기본값: llama3.2]: ").strip()
            self.config["LOCAL_LLM_MODEL"] = model if model else "llama3.2"

    def ask_integrations(self):
        print("\n3. 외부 연동 설정을 진행하시겠습니까?")
        
        # Slack
        use_slack = input("Slack과 연동하시겠습니까? (y/N): ").strip().lower()
        if use_slack == 'y':
            self.config["SLACK_BOT_TOKEN"] = input("SLACK_BOT_TOKEN 입력 (xoxb-...): ").strip()
            self.config["SLACK_APP_TOKEN"] = input("SLACK_APP_TOKEN 입력 (xapp-...): ").strip()
            self.config["SLACK_CHANNEL"] = input("SLACK_CHANNEL 입력 (C0...): ").strip()
            
        # Notion
        use_notion = input("Notion (Archive 에이전트)과 연동하시겠습니까? (y/N): ").strip().lower()
        if use_notion == 'y':
            self.config["NOTION_TOKEN"] = input("NOTION_TOKEN 입력: ").strip()
            self.config["NOTION_DATABASE_ID"] = input("NOTION_DATABASE_ID 입력: ").strip()

    def generate_secrets(self):
        print("\n4. 보안 키 및 패스워드를 설정합니다.")
        
        # ADMIN_API_KEY
        admin_key = input("ADMIN_API_KEY (입력하지 않으면 자동 생성): ").strip()
        self.config["ADMIN_API_KEY"] = admin_key if admin_key else secrets.token_hex(32)
        
        # CLIENT_API_KEY
        client_key = input("CLIENT_API_KEY (입력하지 않으면 자동 생성): ").strip()
        self.config["CLIENT_API_KEY"] = client_key if client_key else secrets.token_hex(32)
        
        # DISPATCH_HMAC_SECRET
        hmac_key = input("DISPATCH_HMAC_SECRET (입력하지 않으면 자동 생성): ").strip()
        self.config["DISPATCH_HMAC_SECRET"] = hmac_key if hmac_key else secrets.token_hex(32)
        
        # ENCRYPTION_KEY
        enc_key = input("ENCRYPTION_KEY (입력하지 않으면 자동 생성): ").strip()
        self.config["ENCRYPTION_KEY"] = enc_key if enc_key else base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        
        # Redis passwords
        redis_orch = input("REDIS_CASSIOPEIA_PASSWORD (입력하지 않으면 자동 생성): ").strip()
        self.config["REDIS_CASSIOPEIA_PASSWORD"] = redis_orch if redis_orch else secrets.token_hex(16)
        
        redis_comm = input("REDIS_COMMUNITY_PASSWORD (입력하지 않으면 자동 생성): ").strip()
        self.config["REDIS_COMMUNITY_PASSWORD"] = redis_comm if redis_comm else secrets.token_hex(16)

    def save_env(self):
        # 기본 설정값
        redis_pass = self.config.get("REDIS_CASSIOPEIA_PASSWORD", "")
        defaults = {
            "PYTHONPATH": ".",
            "REDIS_URL": f"redis://cassiopeia:{redis_pass}@127.0.0.1:6379",
            "NLU_CONFIDENCE_THRESHOLD": "0.7",
            "NLU_LLM_TEMPERATURE": "0.2",
            "USER_TIMEZONE": "Asia/Seoul",
            "CORS_ORIGINS": "http://localhost:3000,http://localhost:5173",
            "RESPONSE_TIMEOUT_SEC": "30.0",
            "CB_THRESHOLD": "3",
            "CB_WINDOW_SEC": "300",
            "HEARTBEAT_VALID_SEC": "30",
            "RATE_LIMIT_PER_MIN": "20",
            "RATE_LIMIT_WINDOW": "60",
            "SANDBOX_RUNTIME": "disabled",
            "SANDBOX_API_KEY": secrets.token_hex(32),
        }
        
        env_lines = [
            "# ══════════════════════════════════════════════════════════════",
            f"#  Generated by Cassiopeia Setup Wizard",
            "# ══════════════════════════════════════════════════════════════\n"
        ]
        
        for key, value in defaults.items():
            if key not in self.config:
                self.config[key] = value
                
        for key, value in self.config.items():
            env_lines.append(f"{key}={value}")
            
        self.env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

if __name__ == "__main__":
    wizard = SetupWizard()
    wizard.run()
