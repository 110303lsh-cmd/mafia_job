from flask import Flask, render_template, request
from flask_socketio import SocketIO, send, emit
import os
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# ------------------------
# 📦 데이터
# ------------------------
users = {}
spectators = set()
roles = {}
alive = set()
votes = {}

phase = "waiting"

mafia_target = None
doctor_target = None

night_actors = set()
night_done = set()

# ------------------------
@app.route('/')
def index():
    return render_template('index.html')

# ------------------------
# 💀 죽은 사람 리스트
# ------------------------
def send_dead_list():
    dead = [
        f"{users[s]} ({roles[s]})"
        for s in users
        if s not in alive
    ]
    socketio.emit('dead_list', dead)

# ------------------------
# 🌙 밤 준비
# ------------------------
def setup_night():
    global night_actors, night_done

    night_done.clear()

    night_actors = {
        s for s in alive
        if roles[s] in ["마피아", "의사", "경찰", "스파이", "도박사"]
    }

    send(f"🌙 행동 필요 인원: {len(night_actors)}명", broadcast=True)

# ------------------------
# 🌙 밤 종료 체크
# ------------------------
def check_night_end():
    if night_done >= night_actors:
        end_night()

# ------------------------
# 👥 입장
# ------------------------
@socketio.on('join')
def handle_join(username):
    username = (username or "").strip()

    if username == "":
        spectators.add(request.sid)
        emit('join_success', to=request.sid)
        emit('spectator', True, to=request.sid)
        return

    if username in users.values():
        emit('error', '이미 사용중!', to=request.sid)
        return

    users[request.sid] = username
    emit('join_success', to=request.sid)

    socketio.emit('user_list', list(users.values()))
    send(f"👤 {username} 입장", broadcast=True)

# ------------------------
# 🎮 게임 시작
# ------------------------
@socketio.on('start_game')
def start_game():
    global roles, alive, phase

    if len(users) < 4:
        emit('error', '최소 4명 필요', to=request.sid)
        return

    sids = list(users.keys())

    role_list = [
        "마피아", "의사", "경찰",
        "기자", "정치인", "스파이",
        "광인", "도박사"
    ] + ["시민"] * max(0, len(sids) - 8)

    random.shuffle(role_list)

    roles.clear()
    alive.clear()
    votes.clear()

    for sid, role in zip(sids, role_list):
        roles[sid] = role
        alive.add(sid)
        emit('role', role, to=sid)

    send_dead_list()

    phase = "night"
    setup_night()

    socketio.emit('phase', 'night')
    socketio.emit('game_started')
    send("🌙 밤 시작", broadcast=True)

# ------------------------
# 💬 채팅
# ------------------------
@socketio.on('message')
def handle_message(msg):
    sid = request.sid

    if sid in spectators:
        return

    role = roles.get(sid)

    # 👻 죽은 사람 채팅
    if sid not in alive:
        for s in users:
            if s not in alive:
                send(f"👻 {users[sid]}: {msg}", to=s)
        return

    # 🕵️ 마피아 채팅
    if msg.startswith("/m "):
        if role not in ["마피아", "스파이"]:
            emit('message', "❌ 마피아 팀만 사용 가능", to=sid)
            return

        mafia_team = [
            s for s in alive
            if roles[s] in ["마피아", "스파이"]
        ]

        for s in mafia_team:
            send(f"🕵️ [팀채팅] {users[sid]}: {msg[3:]}", to=s)
        return

    send(f"{users[sid]}: {msg}", broadcast=True)

# ------------------------
# 🌙 밤 행동
# ------------------------
@socketio.on('night_action')
def night_action(target_name):
    global mafia_target, doctor_target

    sid = request.sid

    if sid not in alive or phase != "night":
        return

    if sid in night_done:
        return

    role = roles[sid]

    target_sid = next((s for s, n in users.items() if n == target_name), None)
    if not target_sid or target_sid not in alive:
        return

    if role in ["마피아", "스파이"]:
        mafia_target = target_sid
        send(f"🕵️ {role} 선택 완료", broadcast=True)

    elif role == "의사":
        doctor_target = target_sid
        send("💉 의사 선택 완료", broadcast=True)

    elif role == "경찰":
        result = "마피아" if roles[target_sid] in ["마피아", "스파이"] else "시민"
        emit('message', f"🔎 {users[target_sid]} → {result}", to=sid)

    night_done.add(sid)
    check_night_end()

# ------------------------
# 🕵️ 스파이 조사
# ------------------------
@socketio.on('spy_check')
def spy_check(target_name):
    sid = request.sid

    if roles.get(sid) != "스파이" or phase != "night":
        return

    if sid in night_done:
        return

    target_sid = next((s for s, n in users.items() if n == target_name), None)
    if not target_sid or target_sid not in alive:
        return

    emit('message', f"🕵️ {users[target_sid]} 직업: {roles[target_sid]}", to=sid)

    night_done.add(sid)
    check_night_end()

# ------------------------
# 🎲 도박사
# ------------------------
@socketio.on('gamble')
def gamble(data):
    sid = request.sid

    if roles.get(sid) != "도박사" or sid in night_done:
        return

    target_name = data.get("target")
    risk = int(data.get("risk"))

    target_sid = next((s for s, n in users.items() if n == target_name), None)
    if not target_sid or target_sid not in alive:
        return

    success = random.randint(1, 100) <= risk

    if success:
        alive.discard(target_sid)
        send(f"🎲 성공! {users[target_sid]} 사망", broadcast=True)
        night_actors.discard(target_sid)
    else:
        alive.discard(sid)
        send(f"💀 실패! {users[sid]} 사망", broadcast=True)
        night_actors.discard(sid)

    night_done.add(sid)
    send_dead_list()

    check_night_end()

# ------------------------
# 🌙 밤 종료
# ------------------------
def end_night():
    global phase, mafia_target, doctor_target

    if mafia_target and mafia_target not in alive:
        mafia_target = None

    if mafia_target:
        if mafia_target == doctor_target:
            send(f"💉 {users[mafia_target]} 생존!", broadcast=True)
        else:
            alive.discard(mafia_target)
            send(f"💀 {users[mafia_target]} 사망", broadcast=True)
    else:
        send("✨ 아무도 안 죽음", broadcast=True)

    mafia_target = None
    doctor_target = None

    send_dead_list()
    check_win()

    if phase == "end":
        return

    phase = "day"
    votes.clear()

    socketio.emit('phase', 'day')
    send("☀️ 낮 시작", broadcast=True)

@socketio.on('report')
def report(target_name):
    sid = request.sid

    # 기자만 가능 + 낮에만
    if roles.get(sid) != "기자" or phase != "day":
        return

    target_sid = next((s for s, n in users.items() if n == target_name), None)
    if not target_sid:
        return

    real_role = roles[target_sid]

    # 🎭 전체 직업 목록
    role_pool = [
        "마피아", "의사", "경찰",
        "기자", "정치인", "스파이",
        "광인", "도박사", "시민"
    ]

    # 🔥 랜덤 결과
    if random.random() < 0.5:
        # 50% 진짜
        shown_role = real_role
        send(f"📰 기자 {users[target_sid]} → {shown_role}", broadcast=True)
    else:
        # 50% 가짜
        fake_pool = [r for r in role_pool if r != real_role]
        shown_role = random.choice(fake_pool)
        send(f"📰 기자 {users[target_sid]} → {shown_role}", broadcast=True)

# ------------------------
# 🗳️ 투표 (비밀투표)
# ------------------------
@socketio.on('vote')
def vote(target_name):
    if request.sid not in alive or phase != "day":
        return

    if request.sid in votes:
        emit('error', '이미 투표함', to=request.sid)
        return

    target_sid = next((s for s, n in users.items() if n == target_name), None)
    if not target_sid or target_sid not in alive:
        return

    votes[request.sid] = target_sid

    emit('message', "🗳️ 투표 완료", to=request.sid)
    send(f"📊 투표 진행: {len(votes)}/{len(alive)}", broadcast=True)

    if len(votes) == len(alive):
        end_day()

def end_day():
    global phase

    count = {}

    for voter, target in votes.items():
        weight = 2 if roles[voter] == "정치인" else 1
        count[target] = count.get(target, 0) + weight

    if count:
        max_votes = max(count.values())
        top = [s for s, c in count.items() if c == max_votes]

        if len(top) == 1:
            victim = top[0]

            if roles[victim] == "광인":
                send(f"🎭 광인 승리!", broadcast=True)
                reveal_roles()
                phase = "end"
                return

            alive.discard(victim)
            send(f"⚰️ {users[victim]} 처형", broadcast=True)

    send_dead_list()
    check_win()

    if phase == "end":
        return

    phase = "night"
    setup_night()

    socketio.emit('phase', 'night')
    send("🌙 밤 시작", broadcast=True)

# ------------------------
# 🏆 승리
# ------------------------
def check_win():
    global phase

    mafia = sum(1 for s in alive if roles[s] in ["마피아", "스파이"])
    citizen = len(alive) - mafia

    if mafia == 0:
        send("🎉 시민 승리!", broadcast=True)
        reveal_roles()
        phase = "end"

    elif mafia >= citizen:
        send("💀 마피아 승리!", broadcast=True)
        reveal_roles()
        phase = "end"

def reveal_roles():
    for s in users:
        send(f"{users[s]} : {roles[s]}", broadcast=True)

# ------------------------
# ❌ 종료
# ------------------------
@socketio.on('disconnect')
def disconnect():
    users.pop(request.sid, None)
    roles.pop(request.sid, None)
    alive.discard(request.sid)
    spectators.discard(request.sid)

    socketio.emit('user_list', list(users.values()))

# ------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)