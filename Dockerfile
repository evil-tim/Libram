# base image
FROM python:3.13-slim

# update system
RUN apt-get update
    && apt-get install -y supervisor \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/log/supervisor

# create non-root user
RUN useradd -m -u 1000 mcp

# set working directory
WORKDIR /app

# install uv
RUN pip install --no-cache-dir uv

# copy project files
COPY . .
RUN ln -s supervisord.conf /etc/supervisord.conf

# uv install dependencies from pyproject.toml
RUN uv sync

# set permissions for mcp user
RUN chown -R mcp:mcp /app && chmod +x entrypoint.sh

# switch to non-root user
USER mcp

# start the MCP
EXPOSE 7778
CMD ["/usr/bin/supervisord"]