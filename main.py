import os
import time
import json
import smtplib
import threading
import requests
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, request, render_template, redirect, url_for,
    session, flash
)
from flask_sqlalchemy import SQLAlchemy
import bcrypt

########################################################################
# Flask 配置
########################################################################

app = Flask(__name__)
app.config['SECRET_KEY'] = '随便输入一些英文加数字东西，也可以使用代码随机生成'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

DB_USER = '数据库用户名'
DB_PASS = '数据库密码'
DB_HOST = '数据库地址'
DB_NAME = '数据库名'

app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}?charset=utf8mb4"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

########################################################################
# 模型
########################################################################

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    email = db.Column(db.String(200))
    smtp_server = db.Column(db.String(100), default='')
    smtp_port = db.Column(db.Integer, default=465)
    smtp_user = db.Column(db.String(100), default='')
    smtp_password = db.Column(db.String(100), default='')
    wechat_webhook = db.Column(db.String(300), default='')

    created_at = db.Column(db.DateTime, default=datetime.now)

class ScheduledTask(db.Model):
    __tablename__ = 'scheduled_tasks'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    task_name = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    remind_date = db.Column(db.String(10), nullable=False)   # YYYY-MM-DD
    remind_time = db.Column(db.String(5), nullable=False)    # HH:MM
    repeat_type = db.Column(db.String(20), default='none')

    advance_days = db.Column(db.String(100), default='')     # "1,2,7"
    advance_status = db.Column(db.Text, default='{}')        # JSON
    has_final_reminded = db.Column(db.Boolean, default=False)

    # 新增字段，标识该任务是否已完全结束
    is_completed = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref='tasks')

    def get_remind_datetime(self):
        dt_str = f"{self.remind_date} {self.remind_time}:00"
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except:
            dt = None
        return dt

    def parse_advance_days(self):
        if not self.advance_days.strip():
            return []
        return [int(x.strip()) for x in self.advance_days.split(',') if x.strip().isdigit()]

    def get_advance_status(self):
        try:
            return json.loads(self.advance_status)
        except:
            return {}

    def set_advance_status(self, st):
        self.advance_status = json.dumps(st)

    def reset_advance_status(self):
        adv_days = self.parse_advance_days()
        new_st = {}
        for d in adv_days:
            new_st[str(d)] = False
        self.set_advance_status(new_st)

    def next_remind_datetime(self):
        dt = self.get_remind_datetime()
        if not dt:
            return None
        now = datetime.now()
        if dt > now:
            return dt

        if self.repeat_type == 'daily':
            while dt <= now:
                dt += timedelta(days=1)
        elif self.repeat_type == 'weekly':
            while dt <= now:
                dt += timedelta(days=7)
        elif self.repeat_type == 'monthly':
            while dt <= now:
                dt += timedelta(days=30)
        else:
            # none
            return None

        self.remind_date = dt.strftime("%Y-%m-%d")
        self.remind_time = dt.strftime("%H:%M")
        self.has_final_reminded = False
        self.is_completed = False
        self.reset_advance_status()
        return dt

########################################################################
# 登录装饰器
########################################################################

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            flash("请先登录")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

########################################################################
# 发送HTML邮件 + 企业微信
########################################################################

def send_email_html(user: User, subject: str, template_name: str, context: dict):
    if not user.smtp_server or not user.smtp_user or not user.email:
        print("用户SMTP或Email未配置，跳过HTML邮件发送")
        return

    body_html = render_template(template_name, **context)
    msg = (
        f"From: {user.smtp_user}\r\n"
        f"To: {user.email}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n\r\n"
        f"{body_html}"
    )
    try:
        with smtplib.SMTP_SSL(user.smtp_server, user.smtp_port) as server:
            server.login(user.smtp_user, user.smtp_password)
            server.sendmail(user.smtp_user, [user.email], msg.encode('utf-8'))
        print("HTML邮件发送成功:", user.email)
    except Exception as e:
        print("HTML邮件发送失败:", e)

def send_wechat_message(user: User, content: str):
    if not user.wechat_webhook:
        print("用户未配置企业微信Webhook，跳过")
        return
    headers = {"Content-Type": "application/json"}
    data = {
        "msgtype": "text",
        "text": {"content": content}
    }
    try:
        r = requests.post(user.wechat_webhook, headers=headers, data=json.dumps(data))
        print("企业微信返回：", r.json())
    except Exception as e:
        print("企业微信发送失败:", e)

########################################################################
# 调度线程
########################################################################

def schedule_loop():
    while True:
        with app.app_context():
            tasks = ScheduledTask.query.all()
            now = datetime.now()

            for task in tasks:
                # 如果已经完成，不再处理
                if task.is_completed:
                    continue

                dt = task.get_remind_datetime()
                if not dt:
                    continue

                # 多个提前天数
                adv_days = task.parse_advance_days()
                adv_status = task.get_advance_status()
                for d in adv_days:
                    if str(d) not in adv_status:
                        adv_status[str(d)] = False

                # 提前提醒
                for d in adv_days:
                    adv_dt = dt - timedelta(days=d)
                    if now >= adv_dt and adv_status[str(d)] == False:
                        context = {
                            "username": task.user.username,
                            "task_name": task.task_name,
                            "message": task.message,
                            "remind_date": task.remind_date,
                            "remind_time": task.remind_time,
                            "repeat_type": task.repeat_type,
                            "note": f"提前 {d} 天提醒"
                        }
                        send_email_html(
                            user=task.user,
                            subject=f"[提前{d}天] {task.task_name}",
                            template_name="email_template.html",
                            context=context
                        )
                        send_wechat_message(
                            task.user,
                            f"【提前{d}天提醒】\n任务：{task.task_name}\n内容：{task.message}"
                        )
                        adv_status[str(d)] = True

                task.set_advance_status(adv_status)

                # 正式提醒
                if now >= dt and not task.has_final_reminded:
                    context = {
                        "username": task.user.username,
                        "task_name": task.task_name,
                        "message": task.message,
                        "remind_date": task.remind_date,
                        "remind_time": task.remind_time,
                        "repeat_type": task.repeat_type,
                        "note": "正式提醒"
                    }
                    send_email_html(
                        user=task.user,
                        subject=f"[正式提醒] {task.task_name}",
                        template_name="email_template.html",
                        context=context
                    )
                    send_wechat_message(
                        task.user,
                        f"【正式提醒】\n任务：{task.task_name}\n内容：{task.message}"
                    )
                    task.has_final_reminded = True

                    if task.repeat_type == 'none':
                        # 该任务彻底完成
                        task.is_completed = True
                    else:
                        nxt = task.next_remind_datetime()
                        if not nxt:
                            task.is_completed = True

                db.session.commit()
        time.sleep(5)

########################################################################
# 路由：注册、登录、个人配置、主页
########################################################################

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash("用户名已存在")
            return redirect(url_for('register'))
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        user = User(username=username, password_hash=hashed.decode('utf-8'))
        db.session.add(user)
        db.session.commit()
        flash("注册成功，请登录")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if not user:
            flash("用户名或密码错误")
            return redirect(url_for('login'))

        if bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
            session['logged_in'] = True
            session['user_id'] = user.id
            flash("登录成功")
            return redirect(url_for('index'))
        else:
            flash("用户名或密码错误")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("您已退出登录")
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    user_id = session['user_id']
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.email = request.form.get('email','')
        user.smtp_server = request.form.get('smtp_server','')
        user.smtp_port = int(request.form.get('smtp_port','465'))
        user.smtp_user = request.form.get('smtp_user','')
        user.smtp_password = request.form.get('smtp_password','')
        user.wechat_webhook = request.form.get('wechat_webhook','')
        db.session.commit()
        flash("个人信息已更新")
        return redirect(url_for('profile'))
    return render_template('profile.html', user=user)

@app.route('/')
@login_required
def index():
    user_id = session['user_id']
    q = request.args.get('q','').strip()

    query = ScheduledTask.query.filter_by(user_id=user_id)
    if q:
        query = query.filter(ScheduledTask.task_name.ilike(f"%{q}%"))

    tasks = query.order_by(ScheduledTask.id.desc()).all()
    return render_template('index.html', tasks=tasks, q=q)

@app.route('/add', methods=['GET','POST'])
@login_required
def add_task():
    possible_advance_days = [1,2,7,14]
    if request.method == 'POST':
        user_id = session['user_id']
        task_name = request.form['task_name']
        message = request.form['message']
        remind_date = request.form['remind_date']
        remind_time = request.form['remind_time']
        repeat_type = request.form.get('repeat_type','none')

        selected_days = request.form.getlist('advance_days')  # ["1", "2", ...]
        advance_days_str = ",".join(selected_days)

        new_task = ScheduledTask(
            user_id=user_id,
            task_name=task_name,
            message=message,
            remind_date=remind_date,
            remind_time=remind_time,
            repeat_type=repeat_type,
            advance_days=advance_days_str
        )
        new_task.reset_advance_status()
        db.session.add(new_task)
        db.session.commit()
        flash("任务已创建")
        return redirect(url_for('index'))

    return render_template('add_task.html', possible_advance_days=possible_advance_days)

@app.route('/edit/<int:task_id>', methods=['GET','POST'])
@login_required
def edit_task(task_id):
    user_id = session['user_id']
    task = ScheduledTask.query.filter_by(id=task_id, user_id=user_id).first_or_404()
    possible_advance_days = [1,2,7,14]

    if request.method == 'POST':
        task.task_name = request.form['task_name']
        task.message = request.form['message']
        task.remind_date = request.form['remind_date']
        task.remind_time = request.form['remind_time']
        task.repeat_type = request.form.get('repeat_type','none')

        selected_days = request.form.getlist('advance_days')
        task.advance_days = ",".join(selected_days)

        task.has_final_reminded = False
        task.is_completed = False
        task.reset_advance_status()

        db.session.commit()
        flash("任务已更新")
        return redirect(url_for('index'))

    all_selected = set(task.parse_advance_days())
    return render_template('edit_task.html', task=task,
                           possible_advance_days=possible_advance_days,
                           all_selected=all_selected)

@app.route('/delete/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    user_id = session['user_id']
    task = ScheduledTask.query.filter_by(id=task_id, user_id=user_id).first_or_404()
    db.session.delete(task)
    db.session.commit()
    flash("任务已删除")
    return redirect(url_for('index'))

def main():
    with app.app_context():
        db.create_all()

    t = threading.Thread(target=schedule_loop, daemon=True)
    t.start()

    app.run(host='0.0.0.0', port=8710, debug=False)

if __name__ == '__main__':
    main()
