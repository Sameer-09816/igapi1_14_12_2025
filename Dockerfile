# Use official lightweight Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# Nodejs is REQUIRED for PyExecJS to run the obfuscated Javascript
# libxml2/libxslt are required for lxml speed
RUN apt-get update && apt-get install -y \
    nodejs \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Expose the port
EXPOSE 8000

# Run the application using Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
