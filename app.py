# 下面是详细中文注释，帮助外行理解每一部分代码
# --------------------
# 依赖库导入，必须提前用 pip 安装，否则程序会报错
# Flask：Web框架，SQLAlchemy：数据库，pandas：处理Excel，pdfplumber：处理PDF，requests：网络请求，flask_cors：跨域支持
# --------------------
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from datetime import datetime
import requests
import json
import os
from enum import Enum
import pandas as pd
import pdfplumber
from werkzeug.utils import secure_filename
from flask_cors import CORS

# --------------------
# Flask应用初始化，配置静态文件和数据库
# --------------------
app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///accounting.db'  # 本地数据库文件
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# 初始化数据库和序列化工具
# --------------------
db = SQLAlchemy(app)
ma = Marshmallow(app)
CORS(app)

# --------------------
# 配置信息，AI密钥和接口地址
# AI_MODEL_API_KEY 是调用AI服务的密钥，已直接写入代码，后端会自动用
# --------------------
class Config:
    AI_MODEL_API_KEY = 'api-key-20250716104705.91e427e8-a542-4e3f-9c8e-5154c54586ae'
    AI_MODEL_ENDPOINT = os.environ.get('AI_MODEL_ENDPOINT', 'https://api.doubao.com/v1/chat/completions')
    SECONDARY_CHECK_MODEL = os.environ.get('SECONDARY_CHECK_MODEL', 'doubao-pro')

# --------------------
# 枚举类型，定义分录类型和状态
# --------------------
class EntryType(Enum):
    DEBIT = '借'
    CREDIT = '贷'

class EntryStatus(Enum):
    PENDING = '待审核'
    APPROVED = '已通过'
    REJECTED = '已拒绝'
    MANUAL = '已人工修改'

# --------------------
# 数据库模型，定义会计分录和训练数据的结构
# --------------------
class AccountingEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_description = db.Column(db.Text, nullable=False)  # 交易描述
    debit_account = db.Column(db.String(100), nullable=False)     # 借方账户
    debit_amount = db.Column(db.Float, nullable=False)            # 借方金额
    credit_account = db.Column(db.String(100), nullable=False)    # 贷方账户
    credit_amount = db.Column(db.Float, nullable=False)           # 贷方金额
    entry_type = db.Column(db.Enum(EntryType), nullable=False)    # 分录类型
    status = db.Column(db.Enum(EntryStatus), default=EntryStatus.PENDING)  # 状态
    created_by = db.Column(db.String(100))                       # 创建人
    created_at = db.Column(db.DateTime, default=datetime.utcnow)  # 创建时间
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)  # 更新时间
    ai_model_used = db.Column(db.String(100))                    # 使用的AI模型
    ai_confidence = db.Column(db.Float)                          # AI置信度
    review_notes = db.Column(db.Text)                            # 审核备注

class TrainingData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_description = db.Column(db.Text, nullable=False)  # 交易描述
    correct_debit_account = db.Column(db.String(100), nullable=False)  # 正确借方账户
    correct_debit_amount = db.Column(db.Float, nullable=False)         # 正确借方金额
    correct_credit_account = db.Column(db.String(100), nullable=False) # 正确贷方账户
    correct_credit_amount = db.Column(db.Float, nullable=False)        # 正确贷方金额
    created_by = db.Column(db.String(100))                            # 创建人
    created_at = db.Column(db.DateTime, default=datetime.utcnow)       # 创建时间

# --------------------
# 序列化器，用于数据库对象和JSON互转
# --------------------
class AccountingEntrySchema(ma.Schema):
    class Meta:
        fields = ('id', 'transaction_description', 'debit_account', 'debit_amount', 
                  'credit_account', 'credit_amount', 'entry_type', 'status', 
                  'created_by', 'created_at', 'updated_at', 'ai_model_used', 
                  'ai_confidence', 'review_notes')

accounting_entry_schema = AccountingEntrySchema()
accounting_entries_schema = AccountingEntrySchema(many=True)

class TrainingDataSchema(ma.Schema):
    class Meta:
        fields = ('id', 'transaction_description', 'correct_debit_account', 
                  'correct_debit_amount', 'correct_credit_account', 'correct_credit_amount',
                  'created_by', 'created_at')

training_data_schema = TrainingDataSchema()
training_datas_schema = TrainingDataSchema(many=True)

# --------------------
# AI服务类，负责和外部AI对接，自动生成分录和二次审核
# --------------------
class AIService:
    def generate_accounting_entry(self, transaction_description):
        """调用AI模型生成会计分录"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {Config.AI_MODEL_API_KEY}'
        }
        # prompt 是给AI的指令，要求AI返回标准格式的分录
        prompt = f"""
        请根据以下交易描述生成标准会计分录，返回格式为JSON：
        {{
            "debit_account": "账户名称",
            "debit_amount": 金额数字,
            "credit_account": "账户名称",
            "credit_amount": 金额数字,
            "explanation": "分录解释"
        }}
        交易描述: {transaction_description}
        """
        data = {
            "model": "doubao-base",
            "messages": [
                {"role": "system", "content": "你是一个专业的会计助手，擅长根据交易描述生成准确的会计分录。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        try:
            response = requests.post(Config.AI_MODEL_ENDPOINT, headers=headers, json=data)
            response.raise_for_status()
            ai_response = response.json()
            # 解析AI返回的分录信息
            entry_data = json.loads(ai_response['choices'][0]['message']['content'])
            # 检查借贷平衡
            if entry_data['debit_amount'] != entry_data['credit_amount']:
                raise ValueError("借贷金额不平衡")
            return {
                'debit_account': entry_data['debit_account'],
                'debit_amount': entry_data['debit_amount'],
                'credit_account': entry_data['credit_account'],
                'credit_amount': entry_data['credit_amount'],
                'ai_model_used': 'doubao-base',
                'ai_confidence': 0.9  # 实际应用中应该从模型获取置信度
            }
        except Exception as e:
            print(f"AI调用错误: {str(e)}")  # 如果AI调用失败，这里会输出详细错误
            return None

    def secondary_audit(self, entry):
        """二次审核校验AI生成的分录"""
        # 这里可以用AI或规则引擎做二次审核，提升准确性
        is_balanced = entry.debit_amount == entry.credit_amount
        # 常见账户配对规则
        common_pairs = [
            ('银行存款', '主营业务收入'),
            ('应收账款', '主营业务收入'),
            ('管理费用', '银行存款'),
            ('库存商品', '银行存款')
        ]
        is_valid_pair = any(
            (entry.debit_account == pair[0] and entry.credit_account == pair[1]) or
            (entry.debit_account == pair[1] and entry.credit_account == pair[0])
            for pair in common_pairs
        )
        # 如果配置了二次AI审核模型，则调用AI再次判断
        if Config.SECONDARY_CHECK_MODEL:
            prompt = f"""
            请审核以下会计分录是否正确，返回"true"或"false"：
            交易描述: {entry.transaction_description}
            借方: {entry.debit_account} {entry.debit_amount}
            贷方: {entry.credit_account} {entry.credit_amount}
            """
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {Config.AI_MODEL_API_KEY}'
            }
            data = {
                "model": Config.SECONDARY_CHECK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个专业的会计审核专家，擅长校验会计分录的准确性。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0
            }
            try:
                response = requests.post(Config.AI_MODEL_ENDPOINT, headers=headers, json=data)
                response.raise_for_status()
                ai_response = response.json()
                ai_approval = ai_response['choices'][0]['message']['content'].strip().lower() == 'true'
                return is_balanced and is_valid_pair and ai_approval
            except Exception as e:
                print(f"二次审核错误: {str(e)}")
                return is_balanced and is_valid_pair
        return is_balanced and is_valid_pair

# --------------------
# 训练服务类，保存人工修正分录，支持AI微调
# --------------------
class TrainingService:
    def add_training_data(self, transaction_description, debit_account, debit_amount, 
                         credit_account, credit_amount, user):
        """添加训练数据"""
        training_data = TrainingData(
            transaction_description=transaction_description,
            correct_debit_account=debit_account,
            correct_debit_amount=debit_amount,
            correct_credit_account=credit_account,
            correct_credit_amount=credit_amount,
            created_by=user
        )
        db.session.add(training_data)
        db.session.commit()
        return training_data
    def retrain_model(self):
        """使用收集的训练数据重新训练模型"""
        # 这里只是示例，实际微调要看AI服务商接口
        training_data = TrainingData.query.all()
        train_dataset = []
        for data in training_data:
            train_dataset.append({
                "transaction_description": data.transaction_description,
                "debit_account": data.correct_debit_account,
                "debit_amount": data.correct_debit_amount,
                "credit_account": data.correct_credit_account,
                "credit_amount": data.correct_credit_amount
            })
        try:
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {Config.AI_MODEL_API_KEY}'
            }
            payload = {
                "model": "doubao-base",
                "training_data": train_dataset,
                "epochs": 3
            }
            response = requests.post(
                "https://api.doubao.com/v1/fine-tune", 
                headers=headers, 
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"模型训练错误: {str(e)}")
            return None

# --------------------
# API路由，定义前端和外部如何访问后端功能
# --------------------
# 生成会计分录
@app.route('/api/entries', methods=['POST'])
def generate_entry():
    """生成会计分录"""
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({'error': '请求体不能为空，且必须为JSON对象'}), 400
    description = data.get('transaction_description')
    if not description:
        return jsonify({'error': '交易描述不能为空'}), 400
    ai_service = AIService()
    entry_data = ai_service.generate_accounting_entry(description)
    if not entry_data:
        return jsonify({'error': '无法生成会计分录'}), 500
    try:
        entry = AccountingEntry(
            transaction_description=description,
            debit_account=entry_data.get('debit_account', ''),
            debit_amount=float(entry_data.get('debit_amount', 0)),
            credit_account=entry_data.get('credit_account', ''),
            credit_amount=float(entry_data.get('credit_amount', 0)),
            entry_type=EntryType.DEBIT,
            status=EntryStatus.PENDING,
            created_by=data.get('created_by', 'system'),
            ai_model_used=entry_data.get('ai_model_used', ''),
            ai_confidence=float(entry_data.get('ai_confidence', 0)),
            review_notes=None
        )
    except Exception as e:
        return jsonify({'error': f'分录数据类型错误: {str(e)}'}), 400
    db.session.add(entry)
    db.session.commit()
    is_approved = ai_service.secondary_audit(entry)
    if is_approved:
        entry.status = EntryStatus.APPROVED
    else:
        entry.status = EntryStatus.REJECTED
        entry.review_notes = "二次审核未通过"
    db.session.commit()
    return accounting_entry_schema.jsonify(entry), 201

# 其它API接口（人工修改、审核、批量导入、训练数据等）都做了类似的判空和异常处理，保证健壮性
# 人工修改会计分录
@app.route('/api/entries/<int:id>', methods=['PUT'])
def update_entry(id):
    """人工修改会计分录"""
    entry = AccountingEntry.query.get_or_404(id)
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({'error': '请求体不能为空，且必须为JSON对象'}), 400
    try:
        entry.debit_account = data.get('debit_account', entry.debit_account)
        entry.debit_amount = float(data.get('debit_amount', entry.debit_amount))
        entry.credit_account = data.get('credit_account', entry.credit_account)
        entry.credit_amount = float(data.get('credit_amount', entry.credit_amount))
    except Exception as e:
        return jsonify({'error': f'分录数据类型错误: {str(e)}'}), 400
    entry.status = EntryStatus.MANUAL
    entry.review_notes = data.get('review_notes', entry.review_notes)
    entry.updated_at = datetime.utcnow()
    db.session.commit()
    if data.get('add_to_training', False):
        training_service = TrainingService()
        training_service.add_training_data(
            entry.transaction_description,
            entry.debit_account,
            entry.debit_amount,
            entry.credit_account,
            entry.credit_amount,
            data.get('updated_by', 'system')
        )
    return accounting_entry_schema.jsonify(entry)

# 审核通过会计分录
@app.route('/api/entries/<int:id>/approve', methods=['POST'])
def approve_entry(id):
    """审核通过会计分录"""
    entry = AccountingEntry.query.get_or_404(id)
    req_json = request.json if request.json else {}
    entry.status = EntryStatus.APPROVED
    entry.review_notes = req_json.get('review_notes', entry.review_notes)
    entry.updated_at = datetime.utcnow()
    db.session.commit()
    return accounting_entry_schema.jsonify(entry)

# 拒绝会计分录
@app.route('/api/entries/<int:id>/reject', methods=['POST'])
def reject_entry(id):
    """拒绝会计分录"""
    entry = AccountingEntry.query.get_or_404(id)
    req_json = request.json if request.json else {}
    entry.status = EntryStatus.REJECTED
    entry.review_notes = req_json.get('review_notes', '被人工拒绝')
    entry.updated_at = datetime.utcnow()
    db.session.commit()
    return accounting_entry_schema.jsonify(entry)

# 添加训练数据
@app.route('/api/training-data', methods=['POST'])
def add_training_data():
    """添加训练数据"""
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({'error': '请求体不能为空，且必须为JSON对象'}), 400
    try:
        training_service = TrainingService()
        training_data = training_service.add_training_data(
            data['transaction_description'],
            data['debit_account'],
            float(data['debit_amount']),
            data['credit_account'],
            float(data['credit_amount']),
            data.get('created_by', 'system')
        )
    except Exception as e:
        return jsonify({'error': f'训练数据类型错误: {str(e)}'}), 400
    return training_data_schema.jsonify(training_data), 201

# 重新训练模型
@app.route('/api/training-data/retrain', methods=['POST'])
def retrain_model():
    """重新训练模型"""
    training_service = TrainingService()
    result = training_service.retrain_model()
    
    if result:
        return jsonify({'message': '模型训练请求已提交', 'result': result}), 200
    else:
        return jsonify({'error': '模型训练失败'}), 500

# 获取所有会计分录
@app.route('/api/entries', methods=['GET'])
def get_entries():
    """获取所有会计分录"""
    status = request.args.get('status')
    if status:
        entries = AccountingEntry.query.filter_by(status=status).all()
    else:
        entries = AccountingEntry.query.all()
    
    return accounting_entries_schema.jsonify(entries)

# 获取单个会计分录
@app.route('/api/entries/<int:id>', methods=['GET'])
def get_entry(id):
    """获取单个会计分录"""
    entry = AccountingEntry.query.get_or_404(id)
    return accounting_entry_schema.jsonify(entry)

# 获取所有训练数据
@app.route('/api/training-data', methods=['GET'])
def get_training_data():
    """获取所有训练数据"""
    training_data = TrainingData.query.all()
    return training_datas_schema.jsonify(training_data)

# --------------------
# 文件上传接口，支持Excel和PDF银行流水批量导入
# --------------------
# 上传的文件必须有“摘要”、“收入”、“支出”这些表头，格式要标准，否则会报错
@app.route('/api/parse-bank-file', methods=['POST'])
def parse_bank_file():
    """解析银行流水Excel或PDF，批量生成分录"""
    if 'file' not in request.files:
        return jsonify({'error': '未上传文件'}), 400
    file = request.files['file']
    if not file or not hasattr(file, 'filename') or not file.filename:
        return jsonify({'error': '文件名无效或未上传文件'}), 400
    filename = secure_filename(file.filename)
    ext = filename.split('.')[-1].lower()
    entries = []
    ai_service = AIService()
    try:
        if ext in ['xls', 'xlsx']:
            df = pd.read_excel(file, header=None)
            header_row = None
            for i, row in df.iterrows():
                if any('摘要' in str(cell) or '收入' in str(cell) or '支出' in str(cell) for cell in row):
                    header_row = i  # 直接用 i，类型为 int
                    break
            if header_row is None:
                return jsonify({'error': '未找到有效的表头行，请检查文件格式'}), 400
            file.seek(0)
            df = pd.read_excel(file, header=header_row)
            df.columns = df.columns.str.strip()
            required_columns = ['摘要', '收入', '支出']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                return jsonify({'error': f'缺少必要的列: {missing_columns}'}), 400
            for idx, row in df.iterrows():
                summary = str(row.get('摘要', ''))
                try:
                    income = float(row.get('收入', 0) or 0)
                    outcome = float(row.get('支出', 0) or 0)
                except Exception:
                    income = 0
                    outcome = 0
                amount = income if income > 0 else outcome
                if amount <= 0:
                    continue
                desc = f"{summary}，金额{amount}元"
                entry_data = ai_service.generate_accounting_entry(desc)
                if entry_data:
                    entries.append({
                        'transaction_description': desc,
                        **entry_data
                    })
        elif ext == 'pdf':
            from io import BytesIO
            file_bytes = BytesIO(file.read())
            file.seek(0)
            with pdfplumber.open(file_bytes) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if not table:
                        continue
                    headers = table[0]
                    for row in table[1:]:
                        row_dict = dict(zip(headers, row))
                        summary = str(row_dict.get('摘要', ''))
                        try:
                            income = float(row_dict.get('收入', 0) or 0)
                            outcome = float(row_dict.get('支出', 0) or 0)
                        except Exception:
                            income = 0
                            outcome = 0
                        amount = income if income > 0 else outcome
                        if amount <= 0:
                            continue
                        desc = f"{summary}，金额{amount}元"
                        entry_data = ai_service.generate_accounting_entry(desc)
                        if entry_data:
                            entries.append({
                                'transaction_description': desc,
                                **entry_data
                            })
        else:
            return jsonify({'error': '仅支持Excel或PDF文件'}), 400
        return jsonify({"entries": entries})
    except Exception as e:
        return jsonify({'error': f'解析或生成分录失败: {str(e)}'}), 500

# --------------------
# 静态文件路由，支持前端页面访问
# --------------------
# 访问 http://127.0.0.1:8080/ 会自动打开 index.html
@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)

# --------------------
# 程序入口，启动Flask服务
# --------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # 自动建表
    app.run(host='0.0.0.0', port=8080, debug=True)  # 启动服务，调试模式