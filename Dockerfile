# The hiring desk, for a team. Put it behind a sign-in proxy: the container
# refuses to serve the name-menu mode on anything but localhost.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# The desk makes no model calls: votes, recap, history and drafts are plain
# Python. The AI fit is computed elsewhere, if at all -- see DEPLOY.md.
# Applications, votes and the desk live here. Mount a volume on it, or every
# redeploy starts from nothing.
VOLUME ["/app/runs"]

EXPOSE 8765
CMD ["python", "desk.py", "serve", "--host", "0.0.0.0", "--port", "8765"]
