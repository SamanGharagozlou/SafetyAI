# human_perception - Docker workflow
#
#   make build                           build the image (CPU PyTorch, yolo26n baked in)
#   make up                              start the stack (laptop webcam, no rviz)
#   make up rviz=true                    ... plus rviz2 showing the detections
#   make up source=d1-wifi rviz=true     AGIBOT D1 camera over Wi-Fi
#   make up source=d1-wifi robot_ip=172.24.1.41   D1 on another network (e.g. event Wi-Fi)
#   make up source=d1-camera robot_ip=<ip>   teammate's d1_camera driver + detector, one container
#   make up source=wsl-webcam follow=true auto_lock=true rviz=true   d1_follow lock-on demo
#   make down | make logs | make shell | make test
#
#   make audit-secrets                   scan the repo for committed credentials
#   make install-hooks                   run that scan on every commit (opt-in)
#
# Variables (all optional):
#   rviz=false|true        start rviz2 (default false)
#   source=webcam          webcam | d1-wifi | d1-ethernet | d1-camera | rtsp | file | wsl-webcam
#   webcam=0               webcam index or /dev/videoN
#   robot_ip=<ip>          D1 address for source=d1-* (default 192.168.234.1 Wi-Fi AP,
#                          192.168.168.168 Ethernet); same name as agibot_d1_bridge's robot_ip
#   rtsp_url=rtsp://...    for source=rtsp (also overrides the D1 defaults)
#   file=/abs/path.mp4     for source=file
#   camera="Integrated Webcam"   Windows camera name for source=wsl-webcam
#   model=yolo26s.pt       detector model (default from config: yolo26n.pt)
#   device=cpu             cpu | cuda:0 (needs a CUDA image: make build torch=cu128)
#   display=$DISPLAY       X display for rviz (WSL + XLaunch: display=localhost:0.0)
#   follow=false|true      also run d1_follow (lock onto one person, dry-run /follow/cmd_vel)
#   auto_lock=false|true   follow=true: lock onto the first person seen (else: lock service)
#   embedder=osnet         follow=true: osnet | histogram
#   domain=0               ROS_DOMAIN_ID
#   torch=cpu              build only: cpu | cu121 | cu128 ...
#   models="yolo26n.pt"    build only: weights baked into the image

rviz     ?= false
source   ?= webcam
webcam   ?= 0
robot_ip ?=
rtsp_url ?=
file     ?=
camera   ?= Integrated Webcam
model    ?=
device   ?=
display  ?= $(DISPLAY)
domain   ?= 0
torch    ?= cpu
models   ?= yolo26n.pt
follow   ?= false
auto_lock ?= false
embedder ?= osnet

MEDIA_DIR   := /tmp/human_perception_media
WSL_FIFO    := $(MEDIA_DIR)/wsl_webcam.mjpeg
SOURCE_TYPE := $(source)
SOURCE_PARAMS :=
FILE_DIR    := $(MEDIA_DIR)
FILE_PATH   := $(file)

ifeq ($(source),d1-wifi)
  SOURCE_TYPE := rtsp
  rtsp_url := $(or $(rtsp_url),rtsp://$(or $(robot_ip),192.168.234.1):8554/test)
  SOURCE_PARAMS := d1_rtsp.yaml
endif
ifeq ($(source),d1-ethernet)
  SOURCE_TYPE := rtsp
  rtsp_url := $(or $(rtsp_url),rtsp://$(or $(robot_ip),192.168.168.168):8554/test)
  SOURCE_PARAMS := d1_rtsp.yaml
endif
ifeq ($(source),d1-camera)
  SOURCE_TYPE := d1_camera
  rtsp_url := $(or $(rtsp_url),rtsp://$(or $(robot_ip),192.168.234.1):8554/test)
endif
ifeq ($(source),file)
  FILE_DIR := $(dir $(abspath $(file)))
  FILE_PATH := $(abspath $(file))
endif
ifeq ($(source),wsl-webcam)
  SOURCE_TYPE := file
  FILE_PATH := $(WSL_FIFO)
  SOURCE_PARAMS := wsl_webcam_pipe.yaml
endif

COMPOSE_ENV = SOURCE_TYPE='$(SOURCE_TYPE)' WEBCAM_DEVICE='$(webcam)' RTSP_URL='$(rtsp_url)' \
	FILE_PATH='$(FILE_PATH)' FILE_DIR='$(FILE_DIR)' SOURCE_PARAMS='$(SOURCE_PARAMS)' \
	MODEL_NAME='$(model)' DEVICE='$(device)' RVIZ='$(rviz)' DISPLAY='$(display)' \
	ROS_DOMAIN_ID='$(domain)' FOLLOW='$(follow)' AUTO_LOCK='$(auto_lock)' EMBEDDER='$(embedder)'

.PHONY: help build up down restart logs shell test audit-secrets install-hooks

help:
	@sed -n '1,36p' Makefile | grep '^#' | sed 's/^# \{0,1\}//'

build:
	TORCH_INDEX_URL=https://download.pytorch.org/whl/$(torch) PRELOAD_MODELS='$(models)' \
		docker compose build

up:
	@case "$(source)" in webcam|d1-wifi|d1-ethernet|d1-camera|rtsp|file|wsl-webcam) ;; \
		*) echo "unknown source=$(source)"; exit 2;; esac
	@if { [ "$(SOURCE_TYPE)" = rtsp ] || [ "$(SOURCE_TYPE)" = d1_camera ]; } && [ -z "$(rtsp_url)" ]; then echo "source=rtsp needs rtsp_url=..."; exit 2; fi
	@if [ "$(source)" = file ] && [ ! -f "$(FILE_PATH)" ]; then echo "source=file needs file=<existing path>"; exit 2; fi
	@mkdir -p $(MEDIA_DIR)
	@if [ "$(rviz)" = true ] && command -v xhost >/dev/null 2>&1; then xhost +local:root >/dev/null 2>&1 || true; fi
	@if [ "$(source)" = wsl-webcam ]; then scripts/wsl_webcam_pipe.sh start $(WSL_FIFO) "$(camera)"; fi
	$(COMPOSE_ENV) docker compose up -d --force-recreate
	@echo "perception stack up (source=$(source), rviz=$(rviz), follow=$(follow)). Logs: make logs | stop: make down"

down:
	-docker compose down
	@scripts/wsl_webcam_pipe.sh stop $(WSL_FIFO)

restart: down up

logs:
	docker compose logs -f --tail=50

shell:
	docker exec -it human_perception bash

test:
	docker run --rm --entrypoint bash human_perception:humble -c \
		'source /opt/ros/humble/setup.bash && cd /ws && colcon build --packages-select human_perception d1_follow \
		 --event-handlers console_direct- >/dev/null && source install/setup.bash && \
		 ROS_DOMAIN_ID=77 colcon test --packages-select human_perception d1_follow --event-handlers console_direct- --pytest-args -p no:anyio >/dev/null; \
		 colcon test-result --verbose'

# Credential hygiene. Needs no Docker, no ROS and no network, so it is the one
# check that always runs: see scripts/audit_secrets.py for what it looks for
# and what it deliberately cannot prove.
audit-secrets:
	@python3 scripts/audit_secrets.py

install-hooks:
	@scripts/install-git-hooks.sh
