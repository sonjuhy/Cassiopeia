FROM python:3.10-slim

WORKDIR /app

# Install Poetry for dependency management
RUN pip install poetry --no-cache-dir

# Copy the entire project to the container
COPY . /app

# Set environment variables for Poetry
ENV POETRY_VERSION=1.7.1
ENV PATH="/root/.local/bin:$PATH"

# Install dependencies for the cassiopeia agent
# Ensure we are in the correct directory before running poetry install
RUN cd /app/agents/cassiopeia_agent && poetry install --no-root --no-cache --without dev

# Expose the port the app runs on
EXPOSE 49152

# Define the command to run the cassiopeia agent
# Use the corrected module path
CMD ["python", "-m", "agents.cassiopeia_agent.main"]
