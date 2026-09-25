FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

WORKDIR /opt/ComfyUI
COPY requirements.txt /tmp/comfyui-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/comfyui-requirements.txt
COPY . /opt/ComfyUI
RUN useradd --uid 10001 --create-home comfyui \
    && mkdir -p /opt/ComfyUI/output /opt/ComfyUI/temp /opt/ComfyUI/user \
    && chown -R comfyui:comfyui /opt/ComfyUI
USER comfyui

CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--disable-auto-launch", "--lowvram"]
