FROM ghcr.io/loong64/nginx:1.29.8-debian-perl

COPY vue-project/dist/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
