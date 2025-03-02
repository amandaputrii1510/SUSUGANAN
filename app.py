import eventlet
eventlet.monkey_patch()
from flask_socketio import SocketIO, emit
from flask import Flask, render_template, request, jsonify
from collections import defaultdict
from datetime import datetime, timedelta
import sys
import logging
import json
import time
import threading
import subprocess
import os



# Inisialisasi Flask + WebSocket
app = Flask(__name__)
# Tambahin CORS biar frontend bisa connect
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")


# Data untuk menyimpan status stream
active_streams = {}
scheduled_streams = {}
videos = {}

# Setup logging biar error gampang dilacak
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Log ketika server mulai
logging.info("Server Flask dengan WebSocket telah dimulai!")


# Fungsi untuk menjalankan FFmpeg sebagai subprocess
def run_ffmpeg_command(command):
    logging.info(f"Running FFmpeg command: {' '.join(command)}")
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, universal_newlines=True)

        out = ''
        err = ''

        # Menerima output secara real-time
        for line in process.stdout:
            out += line
            logging.debug(f"FFmpeg Output: {line.strip()}")  # Log output

        for line in process.stderr:
            err += line
            logging.error(f"FFmpeg Error: {line.strip()}")  # Log error

        process.wait()  # Menunggu proses selesai

        return out, err
    except subprocess.TimeoutExpired:
        logging.error("FFmpeg command timed out!")
        process.kill()
        return None, "FFmpeg command timed out"
    except Exception as e:
        logging.error(f"Exception occurred while running FFmpeg command: {e}")
        return None, str(e)






# Fungsi untuk memulai live stream


def start_live_stream(video_id, stream_key, loop, socketio):
    try:
        if video_id.startswith("D:/"):
            video_url = video_id
        else:
            video_url = videos.get(int(video_id))

        # Cek apakah video ada di path yang diberikan
        if not os.path.exists(video_url):
            logging.error(f"Video tidak ditemukan di path: {video_url}")
            return

        if not video_url:
            logging.error(f"Video dengan id {video_id} tidak ditemukan.")
            return


        logging.info(
            f"Memulai stream dengan Video: {video_id}, URL: {video_url}, Stream Key: {stream_key}")

        command = [
            "ffmpeg", "-loglevel", "debug", "-re", "-i", video_url,
            "-c:v", "libx264", "-preset", "veryfast", "-maxrate", "3000k", "-bufsize", "6000k",
            "-pix_fmt", "yuv420p", "-g", "50", "-c:a", "aac", "-b:a", "160k", "-f", "flv",
            f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
        ]

        logging.info(f"Looping is set to: {loop}")

        if loop:
            command.append("-stream_loop")
            command.append("-1")

        # Kirim status ke frontend
        socketio.emit("stream_status", {
                      "video_id": video_id, "status": "running"})

        out, err = run_ffmpeg_command(command)

        if err:
            logging.error(f"Error dalam streaming: {err}")
            socketio.emit("stream_status", {
                          "video_id": video_id, "status": "error"})
        else:
            logging.info(f"Stream dimulai dengan sukses.")
            socketio.emit("stream_status", {
                          "video_id": video_id, "status": "success"})

    except Exception as e:
        logging.error(f"Error saat memulai live stream: {e}")
        socketio.emit("stream_status", {
                      "video_id": video_id, "status": "error"})


def start_stream_task(stream_id, video_url, stream_key, loop, start_time):
    delay = (start_time - datetime.now()).total_seconds()

    if delay > 0:
        logging.info(
            f"Stream {stream_id} is scheduled to start in {delay} seconds.")
        time.sleep(delay)

    # Validasi video_url dan stream_key
    if not video_url or not stream_key:
        logging.error(
            f"Video URL atau Stream Key tidak valid: {video_url}, {stream_key}")
        return

    command = [
        "ffmpeg", "-loglevel", "debug", "-re", "-i", video_url,
        "-c:v", "libx264", "-preset", "veryfast", "-maxrate", "3000k", "-bufsize", "6000k",
        "-pix_fmt", "yuv420p", "-g", "50", "-c:a", "aac", "-b:a", "160k", "-f", "flv",
        f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    ]

    # Log command untuk memastikan
    logging.debug(f"FFmpeg Command: {command}")

    # Menjalankan perintah FFmpeg dengan timeout
    out, err = run_ffmpeg_command(command)

    if out and not err:
        # Memperbarui status stream jika stream berhasil dimulai
        active_streams[stream_id] = scheduled_streams.pop(stream_id)
        active_streams[stream_id]["status"] = "running"
        active_streams[stream_id]["start_time"] = datetime.now()
        logging.info(f"Stream {stream_id} has started successfully!")
    else:
        logging.error(f"Failed to start stream {stream_id}. Error: {err}")

    # Memperbarui status setiap detik, pastikan stream benar-benar aktif
    if stream_id in active_streams and active_streams[stream_id]["status"] == "running":
        while active_streams[stream_id]["status"] == "running":
            time.sleep(1)
            elapsed_time = (datetime.now() -
                            active_streams[stream_id]["start_time"]).seconds
            active_streams[stream_id]["elapsed_time"] = elapsed_time
            logging.info(
                f"Stream {stream_id} is running for {elapsed_time} seconds.")

# Endpoint untuk mendapatkan status stream
@app.route("/get_stream_status", methods=["GET"])
def get_stream_status():
    active = []
    scheduled = []

    # Menyusun data untuk stream aktif
    for stream_id, stream in active_streams.items():
        active.append({
            "stream_id": stream_id,  # Tambahkan stream_id
            "name": stream["name"],
            "video": stream["video"],
            "status": "Aktif" if stream["status"] == "running" else "Tidak Aktif",
            "elapsed_time": stream.get("elapsed_time", 0)
        })

    # Menyusun data untuk stream yang dijadwalkan
    for stream_id, stream in scheduled_streams.items():
        scheduled.append({
            "stream_id": stream_id,  # Tambahkan stream_id
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
    video_name = request.form["name"]
    video_url = request.form["url"]

    # Menyimpan video dalam data
    videos[video_name] = video_url
    logging.info(f"Video saved: {video_name} at {video_url}")
    return jsonify({"message": "Video saved!"})

# Endpoint untuk menjadwalkan live stream


@app.route("/schedule_live", methods=["POST"])
def schedule_live():
    video_id = request.form["videoId"]
    stream_key = request.form["streamKey"]
    start_time = datetime.strptime(request.form["startTime"], "%Y-%m-%dT%H:%M")
    end_time = datetime.strptime(request.form["endTime"], "%Y-%m-%dT%H:%M")
    loop = request.form["loop"] == "true"

    # Menyusun data jadwal live
    stream_id = len(scheduled_streams) + 1
    scheduled_streams[stream_id] = {
        "name": f"Stream {stream_id}",
        "video": videos.get(video_id),
        "stream_key": stream_key,
        "start_time": start_time,
        "end_time": end_time,
        "loop": loop
    }

    # Menentukan delay sampai waktu start_time
    delay = (start_time - datetime.now()).total_seconds()

    if delay > 0:
        # Jika waktu belum tiba, jadwalkan untuk menunggu
        threading.Timer(delay, start_stream_task, args=(
            stream_id, videos.get(video_id), stream_key, loop, start_time)).start()
        logging.info(
            f"Stream {stream_id} scheduled to start in {delay} seconds.")
    else:
        # Jika waktu sudah lewat, langsung mulai stream
        start_stream_task(stream_id, videos.get(video_id),
                          stream_key, loop, start_time)

    logging.info(f"Stream {stream_id} scheduled.")
    return jsonify({"message": "Stream scheduled!"})

# Endpoint untuk menghentikan live stream


@app.route("/stop_live", methods=["POST"])
def stop_live():
    stream_id = request.form["streamId"]

    if stream_id in active_streams:
        # Hentikan proses FFmpeg biar stream di YouTube beneran mati
        active_streams[stream_id]["process"].terminate()
        # Pastikan proses FFmpeg selesai
        active_streams[stream_id]["process"].wait()
        del active_streams[stream_id]  # Hapus dari daftar stream aktif

        logging.info(f"Stream {stream_id} dihentikan!")

        # Kirim update ke frontend biar UI langsung berubah
        socketio.emit("update_status", {
                      "stream_id": stream_id, "status": "stopped"}, broadcast=True)

        return jsonify({"message": f"Stream {stream_id} benar-benar dihentikan!"})

    logging.warning(f"Stream {stream_id} tidak ditemukan.")
    return jsonify({"error": "Stream tidak ditemukan!"})


# Endpoint untuk menghapus stream


@app.route("/delete_stream", methods=["POST"])
def delete_stream():
    try:
        # Mengonversi stream_id menjadi integer
        stream_id = int(request.form["stream_id"])
        logging.info(f"Received request to delete stream with ID: {stream_id}")

        # Tambahkan log untuk memeriksa apakah stream_id ada di scheduled_streams
        # Log untuk melihat scheduled_streams
        logging.info(f"Scheduled Streams: {scheduled_streams}")
        if stream_id in active_streams:
            del active_streams[stream_id]
            logging.info(f"Stream {stream_id} deleted from active streams.")
            return jsonify({"message": f"Stream {stream_id} deleted!"})
        elif stream_id in scheduled_streams:
            del scheduled_streams[stream_id]
            logging.info(f"Stream {stream_id} deleted from scheduled streams.")
            return jsonify({"message": f"Stream {stream_id} deleted from schedule!"})

        logging.warning(f"Stream {stream_id} not found.")
        return jsonify({"error": "Stream not found!"})
    except Exception as e:
        logging.error(f"Error while deleting stream: {e}")
        return jsonify({"error": "Internal server error"})


# Endpoint untuk memulai stream langsung


@app.route("/start_live", methods=["POST"])
@app.route("/start_live", methods=["POST"])
def start_live():
    try:
        stream_id = request.form["streamId"]
        video_id = request.form["videoId"]
        stream_key = request.form["streamKey"]

        logging.info(
            f"Received streamId: {stream_id}, videoId: {video_id}, streamKey: {stream_key}")

        if video_id.startswith("D:/"):
            video_url = video_id
        else:
            try:
                video_url = videos.get(int(video_id))
            except ValueError:
                logging.error(f"Invalid video ID: {video_id}")
                return jsonify({"error": "Invalid video ID!"})

        if not video_url:
            logging.error(f"Video with id {video_id} not found.")
            return jsonify({"error": "Video not found!"})

        logging.info(f"Video URL for videoId {video_id}: {video_url}")

        loop = False
        start_live_stream(video_url, stream_key, loop, socketio)

        active_streams[stream_id] = {
            "name": f"Stream {stream_id}",
            "video": video_id,
            "status": "running",
            "elapsed_time": 0
        }

        # Kirim update ke frontend
        socketio.emit("stream_status", {
                      "stream_id": stream_id, "status": "running"}, broadcast=True)

        return jsonify({"message": "Live stream started!"})

    except Exception as e:
        logging.error(f"Error while starting stream: {e}")
        return jsonify({"error": "Internal server error"})


# Endpoint untuk mendapatkan daftar video


@app.route("/get_videos", methods=["GET"])
def get_videos():
    video_list = [{"name": name, "url": url} for name, url in videos.items()]
    logging.info("Fetching list of videos.")
    return jsonify({"videos": video_list})


@socketio.on("request_status")
def handle_status_request():
    """Mengirim status terbaru ke frontend"""
    active = []
    scheduled = []

    # Menyusun data untuk stream aktif
    for stream_id, stream in active_streams.items():
        active.append({
            "stream_id": stream_id,
            "name": stream["name"],
            "video": stream["video"],
            "status": "Aktif" if stream["status"] == "running" else "Tidak Aktif",
            "elapsed_time": stream.get("elapsed_time", 0)
        })

    # Menyusun data untuk stream yang dijadwalkan
    for stream_id, stream in scheduled_streams.items():
        scheduled.append({
            "stream_id": stream_id,
            "name": stream["name"],
            "video": stream["video"],
            "status": "Dijadwalkan",
            "start_time": stream["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": stream["end_time"].strftime("%Y-%m-%d %H:%M:%S")
        })

    # Kirim data langsung dengan format yang diharapkan frontend
    socketio.emit("update_status", {
        "activeStreams": active,
        "scheduledStreams": scheduled
    })


@socketio.on("connect")
def handle_connect():
    print("Client connected")


@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")


@app.route("/")
def index():
    return render_template("index.html")

# Inisialisasi untuk mengisi data dummy saat aplikasi pertama kali dijalankan


def initialize_data():
    # Menambahkan beberapa video dummy untuk testing
    videos[1] = "D:/livebot/sunset-bali.mp4"
    videos[2] = "D:/livebot/morning-beach.mp4"
    videos[3] = "D:/livebot/bali-waterfall.mp4"
    videos[4] = "D:/livebot/night-sky.mp4"
    videos[5] = "D:/livebot/surfing-bali.mp4"

    # Menambahkan beberapa stream jadwal dummy
    scheduled_streams[1] = {
        "name": "Stream 1",
        "video": 1,  # ID video
        "stream_key": "dummy_stream_key_1",
        "start_time": datetime.now() + timedelta(seconds=10),
        "end_time": datetime.now() + timedelta(minutes=30),
        "loop": False
    }

    scheduled_streams[2] = {
        "name": "Stream 2",
        "video": 2,  # ID video
        "stream_key": "dummy_stream_key_2",
        "start_time": datetime.now() + timedelta(seconds=20),
        "end_time": datetime.now() + timedelta(minutes=45),
        "loop": True
    }

    scheduled_streams[3] = {
        "name": "Stream 3",
        "video": 3,  # ID video
        "stream_key": "dummy_stream_key_3",
        "start_time": datetime.now() + timedelta(minutes=1),
        "end_time": datetime.now() + timedelta(minutes=20),
        "loop": False
    }

    scheduled_streams[4] = {
        "name": "Stream 4",
        "video": 4,  # ID video
        "stream_key": "dummy_stream_key_4",
        "start_time": datetime.now() + timedelta(minutes=2),
        "end_time": datetime.now() + timedelta(minutes=25),
        "loop": True
    }

    scheduled_streams[5] = {
        "name": "Stream 5",
        "video": 5,  # ID video
        "stream_key": "dummy_stream_key_5",
        "start_time": datetime.now() + timedelta(minutes=3),
        "end_time": datetime.now() + timedelta(minutes=30),
        "loop": False
    }


initialize_data()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
