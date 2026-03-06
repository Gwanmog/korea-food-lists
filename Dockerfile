# 1. Start with a Debian-based Node image (Alpine breaks FAISS)
FROM node:18-bullseye-slim

# 2. Install Python 3 and pip
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 3. Set the working directory
WORKDIR /app

# 4. Install Python dependencies
RUN pip3 install --no-cache-dir faiss-cpu numpy

# 5. Copy the Node package files and install Node modules
COPY soul-food-api/package*.json ./soul-food-api/
RUN cd soul-food-api && npm install

# 6. Copy EVERYTHING else into the container (site, data, python scripts, node scripts)
COPY . .

# 7. Expose the port
EXPOSE 3000

# 8. Start the server
CMD ["node", "soul-food-api/server.js"]