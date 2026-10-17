// Run with: pm2 start ecosystem.config.js
// Telegram credentials belong in .env; do not put them in this file.
const path = require("node:path");

module.exports = {
  apps: [
    {
      name: "animebay-router",
      cwd: __dirname,
      script: path.join(__dirname, ".venv", "bin", "python"),
      args: ["-m", "anime_router"],
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      watch: false,
      min_uptime: "30s",
      max_restarts: 10,
      restart_delay: 15000,
      kill_timeout: 30000,
      merge_logs: true,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONDONTWRITEBYTECODE: "1",
      },
    },
  ],
};
