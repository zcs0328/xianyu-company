FROM python:3.10-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir uvicorn fastapi pydantic pydantic-settings pyyaml loguru sqlalchemy aiosqlite openai httpx apscheduler websockets python-dotenv
EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
