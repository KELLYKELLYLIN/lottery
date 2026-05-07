from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import sqlite3
import random
import string
from datetime import datetime, timedelta
import threading
import time
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'
CORS(app)

# ==================== 配置区域（按需修改） ====================
TOTAL_PRIZES = 100                    # 总奖品数量
START_TIME = "2026-05-07 15:00:00"   # 活动开始时间（改成你想要的）
PRIZE_NAME = "神秘大礼包"             # 奖品名称

# ==================== 数据库初始化 ====================
def init_db():
    conn = sqlite3.connect('lottery.db')
    c = conn.cursor()
    
    # 用户抽奖记录表
    c.execute('''
        CREATE TABLE IF NOT EXISTS lottery_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE,           -- 用户唯一标识（微信OpenID或浏览器指纹）
            nickname TEXT,                 -- 用户昵称（可选）
            is_winner INTEGER DEFAULT 0,   -- 是否中奖 0=未中奖 1=已中奖
            prize_code TEXT,               -- 兑换码（中奖时生成）
            draw_time TEXT,                -- 抽奖时间
            claim_status INTEGER DEFAULT 0,-- 核销状态 0=未领取 1=已领取
            claim_time TEXT                -- 核销时间
        )
    ''')
    
    # 奖品库存表
    c.execute('''
        CREATE TABLE IF NOT EXISTS prize_stock (
            id INTEGER PRIMARY KEY,
            total INTEGER,
            remaining INTEGER
        )
    ''')
    
    # 初始化奖品库存
    c.execute('SELECT * FROM prize_stock WHERE id = 1')
    if not c.fetchone():
        c.execute('INSERT INTO prize_stock (id, total, remaining) VALUES (1, ?, ?)',
                  (TOTAL_PRIZES, TOTAL_PRIZES))
    
    # 管理员/工作人员账号
    c.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT
        )
    ''')
    
    # 初始化管理员账号（只创建一次）
    c.execute('SELECT * FROM admin_users WHERE username = ?', ('admin',))
    if not c.fetchone():
        c.execute('INSERT INTO admin_users (username, password) VALUES (?, ?)',
                  ('admin', '123456'))  # 默认密码，记得改！
    
    conn.commit()
    conn.close()

# ==================== 工具函数 ====================
def get_db():
    conn = sqlite3.connect('lottery.db')
    conn.row_factory = sqlite3.Row
    return conn

def generate_prize_code():
    """生成6位数字兑换码"""
    return ''.join(random.choices('0123456789', k=6))

def get_client_id():
    """获取客户端唯一标识"""
    # 优先使用session中的用户ID
    if 'user_id' in session:
        return session['user_id']
    # 如果没有，生成一个新的
    user_id = 'user_' + ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    session['user_id'] = user_id
    return user_id

def is_activity_active():
    """检查活动是否已开始"""
    start_time = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    return now >= start_time

def get_remaining_prizes():
    """获取剩余奖品数量"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT remaining FROM prize_stock WHERE id = 1')
    row = c.fetchone()
    conn.close()
    return row['remaining'] if row else 0

def get_total_winners():
    """获取已中奖人数"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) as count FROM lottery_records WHERE is_winner = 1')
    row = c.fetchone()
    conn.close()
    return row['count'] if row else 0

# ==================== 路由 ====================

@app.route('/')
def index():
    """用户抽奖页面"""
    return render_template('index.html')

@app.route('/admin')
def admin_page():
    """管理端页面"""
    return render_template('admin.html')

@app.route('/api/check-status', methods=['GET'])
def check_status():
    """检查用户状态和活动状态"""
    user_id = get_client_id()
    activity_active = is_activity_active()
    remaining = get_remaining_prizes()
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM lottery_records WHERE user_id = ?', (user_id,))
    record = c.fetchone()
    conn.close()
    
    result = {
        'activity_active': activity_active,
        'remaining': remaining,
        'has_drawn': False,
        'is_winner': False,
        'prize_code': None,
        'claim_status': None,
        'start_time': START_TIME
    }
    
    if record:
        result['has_drawn'] = True
        result['is_winner'] = bool(record['is_winner'])
        if record['is_winner']:
            result['prize_code'] = record['prize_code']
            result['claim_status'] = record['claim_status']
    
    return jsonify(result)

@app.route('/api/draw', methods=['POST'])
def draw():
    """执行抽奖"""
    user_id = get_client_id()
    
    # 检查活动是否已开始
    if not is_activity_active():
        start_time = datetime.strptime(START_TIME, "%Y-%m-%d %H:%M:%S")
        return jsonify({
            'success': False,
            'message': f'活动尚未开始，开始时间：{start_time.strftime("%Y年%m月%d日 %H:%M:%S")}',
            'status': 'not_started'
        })
    
    # 检查是否已参与过
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM lottery_records WHERE user_id = ?', (user_id,))
    existing = c.fetchone()
    
    if existing:
        result = {
            'success': False,
            'message': '您已经参与过抽奖了',
            'status': 'already_drawn',
            'is_winner': bool(existing['is_winner']),
            'prize_code': existing['prize_code'] if existing['is_winner'] else None,
            'claim_status': existing['claim_status'] if existing['is_winner'] else None
        }
        conn.close()
        return jsonify(result)
    
    # 检查库存
    c.execute('SELECT remaining FROM prize_stock WHERE id = 1')
    stock = c.fetchone()
    
    if stock['remaining'] <= 0:
        # 奖品已抢完
        c.execute('INSERT INTO lottery_records (user_id, is_winner, prize_code, draw_time) VALUES (?, 0, NULL, ?)',
                  (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'message': '好遗憾，奖品已经被抢完了，明天再来吧~',
            'status': 'out_of_stock',
            'is_winner': False
        })
    
    # 中奖！扣库存
    prize_code = generate_prize_code()
    # 确保兑换码唯一
    c.execute('SELECT COUNT(*) as cnt FROM lottery_records WHERE prize_code = ?', (prize_code,))
    while c.fetchone()['cnt'] > 0:
        prize_code = generate_prize_code()
        c.execute('SELECT COUNT(*) as cnt FROM lottery_records WHERE prize_code = ?', (prize_code,))
    
    c.execute('UPDATE prize_stock SET remaining = remaining - 1 WHERE id = 1')
    c.execute('''INSERT INTO lottery_records 
                 (user_id, is_winner, prize_code, draw_time, claim_status) 
                 VALUES (?, 1, ?, ?, 0)''',
              (user_id, prize_code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'message': f'🎉 恭喜您抽中了{PRIZE_NAME}！',
        'status': 'winner',
        'is_winner': True,
        'prize_code': prize_code,
        'claim_status': 0
    })

# ==================== 管理端 API ====================

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """管理员登录"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM admin_users WHERE username = ? AND password = ?', (username, password))
    admin = c.fetchone()
    conn.close()
    
    if admin:
        session['admin_logged_in'] = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '账号或密码错误'})

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    """管理员退出"""
    session.pop('admin_logged_in', None)
    return jsonify({'success': True})

@app.route('/api/admin/check-auth', methods=['GET'])
def check_admin_auth():
    """检查管理员登录状态"""
    return jsonify({'logged_in': session.get('admin_logged_in', False)})

@app.route('/api/admin/verify-code', methods=['POST'])
def verify_code():
    """核销兑换码"""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '请先登录'})
    
    data = request.json
    code = data.get('code')
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM lottery_records WHERE prize_code = ? AND is_winner = 1', (code,))
    record = c.fetchone()
    
    if not record:
        conn.close()
        return jsonify({'success': False, 'message': '无效的兑换码'})
    
    if record['claim_status'] == 1:
        conn.close()
        return jsonify({
            'success': False,
            'message': f'该兑换码已于 {record["claim_time"]} 核销，请勿重复领取',
            'already_claimed': True
        })
    
    # 标记为已领取
    claim_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('UPDATE lottery_records SET claim_status = 1, claim_time = ? WHERE prize_code = ?',
              (claim_time, code))
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'message': f'✅ 核销成功！兑换码：{code}',
        'claim_time': claim_time
    })

@app.route('/api/admin/records', methods=['GET'])
def get_records():
    """获取所有抽奖记录"""
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': '请先登录'})
    
    conn = get_db()
    c = conn.cursor()
    
    # 获取统计信息
    total_winners = get_total_winners()
    remaining = get_remaining_prizes()
    claimed_count = 0
    c.execute('SELECT COUNT(*) as cnt FROM lottery_records WHERE claim_status = 1')
    row = c.fetchone()
    claimed_count = row['cnt']
    
    # 获取所有记录
    c.execute('''SELECT user_id, is_winner, prize_code, draw_time, claim_status, claim_time 
                 FROM lottery_records ORDER BY draw_time DESC''')
    records = [dict(row) for row in c.fetchall()]
    
    # 添加排序权重（中奖+已领取排最后）
    for r in records:
        if r['is_winner'] and r['claim_status'] == 0:
            r['sort_order'] = 0  # 中奖未领取的排最前
        elif r['is_winner'] and r['claim_status'] == 1:
            r['sort_order'] = 2  # 中奖已领取的排最后
        else:
            r['sort_order'] = 1  # 未中奖排中间
    
    records.sort(key=lambda x: x['sort_order'])
    
    conn.close()
    
    return jsonify({
        'success': True,
        'stats': {
            'total_prizes': TOTAL_PRIZES,
            'remaining': remaining,
            'total_winners': total_winners,
            'claimed_count': claimed_count
        },
        'records': records
    })

# ==================== 启动应用 ====================
if __name__ == '__main__':
    init_db()
    print(f"🎉 抽奖系统已启动！")
    print(f"📱 用户端地址：http://localhost:5000")
    print(f"🔧 管理端地址：http://localhost:5000/admin")
    print(f"🔑 管理员账号：admin / 123456（请及时修改）")
    print(f"⏰ 活动开始时间：{START_TIME}")
    app.run(host='0.0.0.0', port=5000, debug=True)