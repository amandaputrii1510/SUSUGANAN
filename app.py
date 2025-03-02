import os
from datetime import datetime, timedelta
import eventlet
eventlet.monkey_patch()
from flask_socketio import SocketIO, emit
from flask import Flask, render_template, request, jsonify
from datetime import datetime
import logging
import time
import threading
import subprocess



# Inisialisasi Flask + WebSocket
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Data untuk menyimpan status stream
active_streams = {}
scheduled_streams = {}
videos = {}

# Setup logging
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# Log ketika server mulai
logging.info("Server Flask dengan WebSocket telah dimulai!")

# Fungsi untuk menjalankan FFmpeg sebagai subprocess


def run_ffmpeg_command(command):
    logging.info(f"Menjalankan perintah FFmpeg: {' '.join(command)}")
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for line in process.stdout:
        logging.debug(line.decode().strip())
    for line in process.stderr:
        logging.error(line.decode().strip())
    process.communicate()

# Fungsi untuk memulai live stream


def start_live_stream(video_id, stream_key, loop, socketio):
    video_url = videos.get(video_id)
    if not os.path.exists(video_url):
        logging.error(f"Video tidak ditemukan: {video_url}")
        return
    logging.info(
        f"Memulai stream dengan Video: {video_id}, URL: {video_url}, Stream Key: {stream_key}")

    command = [
        "ffmpeg", "-loglevel", "debug", "-re", "-i", video_url,
        "-c:v", "libx264", "-preset", "veryfast", "-maxrate", "3000k", "-bufsize", "6000k",
        "-pix_fmt", "yuv420p", "-g", "50", "-c:a", "aac", "-b:a", "160k", "-f", "flv",
        f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    ]
    thread = threading.Thread(target=run_ffmpeg_command, args=(command,))
    thread.start()
    stream_id = len(active_streams) + 1
    active_streams[stream_id] = {
        "name": f"Stream {stream_id}",
        "video": video_id,
        "status": "running",
        "elapsed_time": 0
    }
    socketio.emit("update_status", {
        "activeStreams": [{"stream_id": stream_id, "name": f"Stream {stream_id}", "video": video_id, "status": "Aktif", "elapsed_time": 0}]
    })

# Fungsi untuk menghentikan live stream


def stop_live_stream(stream_id):
    if stream_id in active_streams:
        active_streams[stream_id]["status"] = "stopped"
        logging.info(f"Stream {stream_id} dihentikan.")
        return {"message": f"Stream {stream_id} dihentikan"}
    return {"error": f"Stream {stream_id} tidak ditemukan"}

# Fungsi untuk memulai live stream berdasarkan waktu yang dijadwalkan


def start_scheduled_stream(stream_id):
    # Memastikan stream dijadwalkan memiliki atribut yang benar
    if stream_id in scheduled_streams:
        stream = scheduled_streams[stream_id]
        video_id = stream["video"]
        stream_key = stream["stream_key"]
        start_time = stream["start_time"]
        end_time = stream["end_time"]
        loop = stream["loop"]

        logging.info(
            f"Memulai stream {stream_id} dengan Video ID: {video_id}, Stream Key: {stream_key}")

        # Panggil fungsi untuk memulai stream
        start_live_stream(video_id, stream_key, loop, socketio)

        # Setelah stream selesai, kita bisa menghapusnya jika tidak ada looping
        if not loop:
            del scheduled_streams[stream_id]
            logging.info(
                f"Stream {stream_id} selesai dan dihapus dari penjadwalan.")
        else:
            logging.info(f"Stream {stream_id} akan diputar ulang.")

# Fungsi untuk menjadwalkan stream dengan waktu tertentu


def schedule_stream(video_id, stream_key, start_time, end_time, loop):
    if video_id not in videos:
        logging.error("Video ID tidak valid!")
        return {"error": "Video ID tidak valid!"}

    stream_id = len(scheduled_streams) + 1
    video_name = videos[video_id]

    scheduled_streams[stream_id] = {
        "name": f"Stream {stream_id} - {video_name}",
        "video": video_id,
        "stream_key": stream_key,
        "start_time": start_time,
        "end_time": end_time,
        "loop": loop
    }

    logging.info(
        f"Stream {stream_id} dijadwalkan untuk {start_time} hingga {end_time}")

    # Menghitung selisih waktu sampai penjadwalan dimulai
    now = datetime.now()
    time_diff = start_time - now
    if time_diff.total_seconds() > 0:
        logging.info(
            f"Penjadwalan stream {stream_id} akan dimulai dalam {time_diff.total_seconds()} detik.")
        threading.Timer(time_diff.total_seconds(),
                        start_scheduled_stream, args=[stream_id]).start()
    else:
        logging.warning(
            f"Stream {stream_id} dijadwalkan di masa lalu, langsung dimulai.")
        start_scheduled_stream(stream_id)

    return {"message": f"Stream {stream_id} dijadwalkan"}



@app.route("/")
def index():
    return render_template("index.html")

# Endpoint untuk mendapatkan status stream


@app.route("/get_stream_status", methods=["GET"])
def get_stream_status():
    active = []
    scheduled = []
    for stream_id, stream in active_streams.items():
        active.append({
            "stream_id": stream_id,
            "name": stream["name"],
            "video": stream["video"],
            "status": "Aktif" if stream["status"] == "running" else "Tidak Aktif",
            "elapsed_time": stream.get("elapsed_time", 0)
        })
    for stream_id, stream in scheduled_streams.items():
        scheduled.append({
            "stream_id": stream_id,
            "name": stream["name"],
            "video": stream["video"],
            "status": "Dijadwalkan",
            "start_time": stream["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": stream["end_time"].strftime("%Y-%m-%d %H:%M:%S")
        })
    return jsonify({"activeStreams": active, "scheduledStreams": scheduled})

# Endpoint untuk menyimpan video


@app.route("/save_video", methods=["POST"])
def save_video():
    video_name = request.form['name']
    video_url = request.form['url']
    videos[video_name] = video_url
    return jsonify({"message": f"Video {video_name} disimpan dengan URL {video_url}"})


@app.route("/get_videos", methods=["GET"])
def get_videos():
    # Menyusun daftar video dengan ID
    video_list = [{"id": idx, "name": name, "url": url}
                  for idx, (name, url) in enumerate(videos.items(), start=1)]
    return jsonify({"videos": video_list})

# Endpoint untuk menjadwalkan live stream


@app.route("/schedule_live", methods=["POST"])
def schedule_live():
    video_id = request.form['videoId']
    stream_key = request.form['streamKey']
    start_time = datetime.fromisoformat(request.form['startTime'])
    end_time = datetime.fromisoformat(request.form['endTime'])
    loop = request.form['loop'] == 'true'

    message = schedule_stream(video_id, stream_key, start_time, end_time, loop)
    return jsonify(message)

# Endpoint untuk memulai stream langsung


@app.route("/start_live", methods=["POST"])
def start_live():
    video_id = request.form['videoId']
    stream_key = request.form['streamKey']
    loop = False  # Default loop off
    start_live_stream(video_id, stream_key, loop, socketio)
    return jsonify({"message": "Stream dimulai!"})

# Endpoint untuk menghentikan live stream


@app.route("/stop_live", methods=["POST"])
def stop_live():
    stream_id = int(request.form['streamId'])
    message = stop_live_stream(stream_id)
    return jsonify(message)

# Endpoint untuk menghapus stream


@app.route("/delete_stream", methods=["POST"])
def delete_stream():
    stream_id = int(request.form['stream_id'])
    if stream_id in active_streams:
        del active_streams[stream_id]
        return jsonify({"message": f"Stream {stream_id} dihapus"})
    elif stream_id in scheduled_streams:
        del scheduled_streams[stream_id]
        return jsonify({"message": f"Stream {stream_id} dihapus"})
    else:
        return jsonify({"error": "Stream tidak ditemukan"})


@socketio.on("connect")
def handle_connect():
    print("Client connected")


@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")


@socketio.on("request_status")
def handle_status_request():
    active = []
    scheduled = []

    # Menyusun data untuk stream aktif
    for stream_id, stream in active_streams.items():
        if "name" in stream and "video" in stream:
            active.append({
                "stream_id": stream_id,
                "name": stream["name"],
                "video": stream["video"],
                "status": "Aktif" if stream["status"] == "running" else "Tidak Aktif",
                "elapsed_time": stream.get("elapsed_time", 0)
            })

    # Menyusun data untuk stream yang dijadwalkan
    for stream_id, stream in scheduled_streams.items():
        if "name" in stream and "video" in stream:
            scheduled.append({
                "stream_id": stream_id,
                "name": stream["name"],
                "video": stream["video"],
                "status": "Dijadwalkan",
                "start_time": stream["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": stream["end_time"].strftime("%Y-%m-%d %H:%M:%S")
            })
        else:
            logging.error(
                f"Stream {stream_id} yang dijadwalkan tidak memiliki atribut 'name' atau 'video'")

    emit("update_status", {"activeStreams": active,
         "scheduledStreams": scheduled})



# Fungsi untuk mengulang stream


def restart_stream(stream_id):
    logging.info(f"Stream {stream_id} dimulai ulang")
    # Logika untuk memulai ulang stream
    start_live_stream(stream_id, socketio)

# WebSocket event listener untuk update otomatis


@socketio.on("update_status")
def update_status(data):
    active = []
    scheduled = []
    for stream_id, stream in active_streams.items():
        active.append({
            "stream_id": stream_id,
            "name": stream["name"],
            "video": stream["video"],
            "status": stream["status"],
            "elapsed_time": stream.get("elapsed_time", 0)
        })
    for stream_id, stream in scheduled_streams.items():
        scheduled.append({
            "stream_id": stream_id,
            "name": stream["name"],
            "video": stream["video"],
            "status": "Dijadwalkan",
            "start_time": stream["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": stream["end_time"].strftime("%Y-%m-%d %H:%M:%S")
        })
    emit("update_status", {"activeStreams": active,
         "scheduledStreams": scheduled})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
