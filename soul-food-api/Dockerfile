# Use a lightweight Node.js image
FROM node:18-alpine

# Set the working directory inside the container
WORKDIR /app

# Copy the "ID Card" first to install libraries (caches better)
COPY package.json .

# Install the libraries inside the container
RUN npm install

# Copy the rest of your code
COPY . .

# Open port 3000 so we can talk to it
EXPOSE 3000

# The command to start the server
CMD ["node", "server.js"]