FROM python:3.12-slim
LABEL io.modelcontextprotocol.server.name="io.github.CYAN-ECLIPSE-TECHNOLOGIES-LLC/cadence"
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY server.py ./
COPY mcp ./mcp
COPY cadence ./cadence
COPY tests/fixtures ./tests/fixtures
RUN pip install --no-cache-dir . \
 && useradd -r appuser \
 && mkdir -p /app/runtime \
 && chown appuser:appuser /app/runtime
ENV CADENCE_MCP_HOME=/app
ENV CADENCE_MCP_STATE_DIR=/app/runtime
USER appuser
ENTRYPOINT ["python", "-m", "server"]
