import os
import pandas as pd
import numpy as np
import requests
import json
import re
import xgboost as xgb
from itertools import combinations_with_replacement

print("🚀 통합 AI 파이프라인 가동 시작...\n")

# --- [1] 메타데이터 및 역할군 사전 로드 ---
API_KEY = os.environ.get("BSER_API_KEY")
if not API_KEY:
    raise ValueError("❌ BSER_API_KEY 환경변수가 설정되지 않았습니다!")

headers = {"x-api-key": API_KEY, "accept": "application/json"}
res_l10n = requests.get("https://open-api.bser.io/v1/l10n/Korean", headers=headers)
char_num_to_name = {}

def normalize_name(name):
    return re.sub(r'[^a-zA-Z0-9가-힣]', '', str(name)).strip()

if res_l10n.status_code == 200:
    res_txt = requests.get(res_l10n.json()['data']['l10Path'])
    res_txt.encoding = 'utf-8'
    for line in res_txt.text.splitlines():
        if line.startswith("Character/Name/"):
            m = re.match(r'^Character/Name/(\d+)\s*[^a-zA-Z0-9가-힣]*\s*(.+)$', line)
            if m: char_num_to_name[int(m.group(1))] = normalize_name(m.group(2))

# 💡 12번, 17번 결번이 반영된 마스터리 하드코딩 사전
weapon_num_to_type = {
    1: '글러브', 2: '톤파', 3: '방망이', 4: '채찍', 5: '투척',
    6: '암기', 7: '활', 8: '석궁', 9: '권총', 10: '돌격소총',
    11: '저격총',
    13: '망치', 14: '도끼', 15: '단검', 16: '양손검',
    18: '쌍검', 19: '창', 20: '쌍절곤', 21: '레이피어', 22: '기타',
    23: '카메라', 24: '아르카나', 25: 'VF의수'
}

name_weapon_to_role = {}
try:
    with open('data.json', 'r', encoding='utf-8') as f:
        for char in json.load(f):
            key = f"{normalize_name(char['name'])}_{normalize_name(char['weapon'])}"
            name_weapon_to_role[key] = char['role']
except FileNotFoundError:
    print("⚠️ 'data.json' 파일을 찾을 수 없습니다.")

# --- [2] 전처리 및 격차(Diff) 변환 ---
print("📥 원본 데이터 전처리 및 격차(Diff) 압축 진행 중...")
df = pd.read_csv('er_makgeumgu_dataset_v3_rescued.csv')

def get_role(char_num, weapon_num):
    if pd.isna(char_num) or pd.isna(weapon_num): return "Unknown"
    norm_name = char_num_to_name.get(int(char_num), "UnknownChar")
    weapon_name = '톤파' if norm_name == '알렉스' else weapon_num_to_type.get(int(weapon_num), "UnknownWeapon")
    combo_key = f"{norm_name}_{normalize_name(weapon_name)}"
    return name_weapon_to_role.get(combo_key, norm_name)

for prefix in ['win', 'lose']:
    for i in range(1, 4):
        df[f"{prefix}_role{i}"] = df.apply(lambda row: get_role(row[f"{prefix}_char{i}"], row[f"{prefix}_weapon{i}"]), axis=1)

roles = ['퓨어탱커', '탱브루저', '딜브루저', '암살자', '서포터', '평원딜', '스증원딜']
dataset = []
np.random.seed(42)

for _, row in df.iterrows():
    win_tran = sum([row[f'win_tran{i}'] for i in range(1,4)])
    lose_tran = sum([row[f'lose_tran{i}'] for i in range(1,4)])
    win_leg = sum([row[f'win_leg{i}'] for i in range(1,4)])
    lose_leg = sum([row[f'lose_leg{i}'] for i in range(1,4)])
    win_lv = sum([row[f'win_lv{i}'] for i in range(1,4)]) / 3.0
    lose_lv = sum([row[f'lose_lv{i}'] for i in range(1,4)]) / 3.0
    
    win_role_list = [row[f'win_role{i}'] for i in range(1,4)]
    lose_role_list = [row[f'lose_role{i}'] for i in range(1,4)]
    
    win_counts = {r: win_role_list.count(r) for r in roles}
    lose_counts = {r: lose_role_list.count(r) for r in roles}
    
    if np.random.rand() > 0.5:
        diff_data = {'Diff_Total_Tran': win_tran - lose_tran, 'Diff_Total_Leg': win_leg - lose_leg, 'Diff_Avg_Level': win_lv - lose_lv, 'Target_Win': 1}
        for r in roles: diff_data[f'Diff_{r}'] = win_counts[r] - lose_counts[r]
    else:
        diff_data = {'Diff_Total_Tran': lose_tran - win_tran, 'Diff_Total_Leg': lose_leg - win_leg, 'Diff_Avg_Level': lose_lv - win_lv, 'Target_Win': 0}
        for r in roles: diff_data[f'Diff_{r}'] = lose_counts[r] - win_counts[r]
    dataset.append(diff_data)
    
diff_df = pd.DataFrame(dataset)

# --- [3] AI 학습 및 가중치 추출 ---
print("⏳ AI 훈련 및 휴리스틱 가중치 계산 중...")
X = diff_df.drop(['Target_Win'], axis=1) 
y = diff_df['Target_Win']

# 💡 모델 튜닝: L2 정규화, 최소 표본 기준, 가중치 산출 방식(weight) 변경
model = xgb.XGBClassifier(
    n_estimators=300, 
    learning_rate=0.05, 
    max_depth=6, 
    random_state=42, 
    eval_metric='logloss',
    importance_type='weight',    # 💡 튀는 승률 대신 '트리 분할에 쓰인 빈도' 기반으로 신뢰도 높은 점수비 도출
    colsample_bytree=0.5,        # 💡 특정 역할군(변수)에 과의존하는 현상 방지
    reg_lambda=50.0,             # 💡 튀는 상대적 가중치를 평균으로 강력하게 억제
    min_child_weight=10          # 💡 표본이 적은 노이즈 데이터는 학습에서 배제
)
model.fit(X, y)

# 💡 weight로 뽑아낸 신뢰도 높은 점수비(feature_importances_)로 복구
imp_dict = dict(zip(X.columns, model.feature_importances_))
scale_factor = 20.0 / imp_dict.get('Diff_Total_Leg', 1)

tran_pts = round(float(imp_dict.get('Diff_Total_Tran', 0) * scale_factor), 2)
individual_level_pts = round(float(imp_dict.get('Diff_Avg_Level', 0) * scale_factor / 3.0), 2)
role_pts_dict = {r: round(float(imp_dict.get(f'Diff_{r}', 0) * scale_factor), 2) for r in roles}

weights_json = {
    "LEGENDARY_ITEM": 20.0,
    "TRANSCENDENT_ITEM": tran_pts,
    "LEVEL_PER_PERSON": individual_level_pts,
    "ROLE_ADVANTAGE": role_pts_dict
}
with open('heuristic_weights.json', 'w', encoding='utf-8') as f:
    json.dump(weights_json, f, ensure_ascii=False, indent=2)

# --- [4] 상성 매트릭스 시뮬레이션 및 추출 ---
print("⏳ 3,570가지 덱 상성 매트릭스 일괄 추출 중...")
decks = list(combinations_with_replacement(roles, 3))
deck_counts = [{r: deck.count(r) for r in roles} for deck in decks]
deck_names = [" + ".join(deck) for deck in decks]

sim_rows = []
pairs = []
for i in range(len(decks)):
    for j in range(i, len(decks)):
        row = {'Diff_Total_Tran': 0, 'Diff_Total_Leg': 0, 'Diff_Avg_Level': 0}
        for r in roles: row[f'Diff_{r}'] = deck_counts[i][r] - deck_counts[j][r]
        sim_rows.append(row)
        pairs.append((i, j))
        
pred_df = pd.DataFrame(sim_rows)
win_rates = np.round(model.predict_proba(pred_df)[:, 1] * 100, 2)

synergy_dict = {}
for idx, (i, j) in enumerate(pairs):
    # 💡 미러전(완전히 동일한 덱)일 경우 무조건 승률 50.0으로 고정
    if i == j:
        win_rate = 50.0
    else:
        win_rate = float(win_rates[idx])
        
    synergy_dict[f"{deck_names[i]} VS {deck_names[j]}"] = win_rate
    synergy_dict[f"{deck_names[j]} VS {deck_names[i]}"] = round(100.0 - win_rate, 2)
    
with open('synergy_matrix.json', 'w', encoding='utf-8') as f:
    json.dump(synergy_dict, f, ensure_ascii=False, indent=2)

print("✅ 통합 파이프라인 무사 통과! JSON 데이터 추출 완료.")
