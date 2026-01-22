FROM python:3.11-slim

LABEL maintainer="AgentTasker Contributors"
LABEL description="AgentTasker MCP Server - Parallel task execution for AI agents"

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY mcp_server.py .
COPY agent_tasker.py .

# Default to 8 workers
ENV WORKERS=8

# Run the MCP server
CMD ["sh", "-c", "python mcp_server.py --workers $WORKERS"]
