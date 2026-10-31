# BloodLink

A blood donation platform built with Flask and MySQL, containerized with Docker for CSC418 (DevOps for Cloud Computing), Assignment 2.

## Modules

1. **Authentication**: sign up, log in, log out, with hashed passwords and protected pages
2. **Donor management**: register or update a donor profile (blood group, city, phone, last donation, availability) and search for donors by blood-group compatibility and city
3. **Blood requests**: post a request with urgency, see matching eligible donors, let donors respond, and mark the request fulfilled
4. **Dashboard**: site-wide stats, supply vs need by blood group, my requests, and my responses

Donors become eligible again 90 days after their last donation, and search applies real ABO/Rh compatibility (for example, AB+ patients see every donor).

## Project layout

```
app/
  app.py            routes for all 4 modules + /health
  db.py             MySQL connection, schema, startup retry
  requirements.txt
  templates/        Jinja pages
  static/style.css
Dockerfile          builds the web image (Part I)
docker-compose.yml  web + MySQL with a persistent volume (Part I)
```

## Run (Part I)

```bash
# build the image (replace with your Docker Hub username), then start the stack
docker build -t ahmadrahman007/bloodlink:latest .
docker compose up -d
```

On EC2 you only need `docker-compose.yml`. Compose pulls the image from Docker Hub.

- App: http://localhost:8000 (container port 5000)
- MySQL: host port 3307 (container port 3306)
- DB data persists in the `bloodlink-db-data` volume
- Health check: http://localhost:8000/health

Tables are created automatically on startup.

Webhook test.
