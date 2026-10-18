FROM oven/bun:1

WORKDIR /app

COPY package.json ./
RUN bun install

COPY . .

EXPOSE 3100

CMD ["bun", "run", "dev", "--host", "0.0.0.0", "--port", "3100"]
