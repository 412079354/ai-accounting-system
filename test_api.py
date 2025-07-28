import requests
import json
import unittest

BASE_URL = 'http://localhost:5000/api'

class TestAccountingAPI(unittest.TestCase):
    def test_generate_entry(self):
        # 测试生成会计分录
        data = {
            "transaction_description": "销售商品收到现金1000元",
            "created_by": "test_user"
        }
        
        response = requests.post(f"{BASE_URL}/entries", json=data)
        self.assertEqual(response.status_code, 201)
        
        entry = response.json()
        self.assertIn('debit_account', entry)
        self.assertIn('credit_account', entry)
        self.assertEqual(entry['status'], '待审核')
        
        return entry
    
    def test_update_entry(self):
        # 先创建一个分录
        entry = self.test_generate_entry()
        
        # 修改分录
        data = {
            "debit_account": "银行存款",
            "debit_amount": 1000.0,
            "credit_account": "主营业务收入",
            "credit_amount": 1000.0,
            "review_notes": "调整会计科目",
            "add_to_training": True,
            "updated_by": "test_user"
        }
        
        response = requests.put(f"{BASE_URL}/entries/{entry['id']}", json=data)
        self.assertEqual(response.status_code, 200)
        
        updated_entry = response.json()
        self.assertEqual(updated_entry['debit_account'], data['debit_account'])
        self.assertEqual(updated_entry['status'], '已人工修改')
    
    def test_approve_entry(self):
        # 先创建一个分录
        entry = self.test_generate_entry()
        
        # 审核通过
        data = {
            "review_notes": "审核通过"
        }
        
        response = requests.post(f"{BASE_URL}/entries/{entry['id']}/approve", json=data)
        self.assertEqual(response.status_code, 200)
        
        approved_entry = response.json()
        self.assertEqual(approved_entry['status'], '已通过')
    
    def test_reject_entry(self):
        # 先创建一个分录
        entry = self.test_generate_entry()
        
        # 拒绝
        data = {
            "review_notes": "科目选择不正确"
        }
        
        response = requests.post(f"{BASE_URL}/entries/{entry['id']}/reject", json=data)
        self.assertEqual(response.status_code, 200)
        
        rejected_entry = response.json()
        self.assertEqual(rejected_entry['status'], '已拒绝')
    
    def test_add_training_data(self):
        # 添加训练数据
        data = {
            "transaction_description": "购买办公用品支出500元",
            "debit_account": "管理费用-办公费",
            "debit_amount": 500.0,
            "credit_account": "库存现金",
            "credit_amount": 500.0,
            "created_by": "test_user"
        }
        
        response = requests.post(f"{BASE_URL}/training-data", json=data)
        self.assertEqual(response.status_code, 201)
        
        training_data = response.json()
        self.assertIn('id', training_data)
        self.assertEqual(training_data['debit_account'], data['debit_account'])
    
    def test_retrain_model(self):
        # 测试重新训练模型
        response = requests.post(f"{BASE_URL}/training-data/retrain")
        self.assertEqual(response.status_code, 200)

if __name__ == '__main__':
    unittest.main()    