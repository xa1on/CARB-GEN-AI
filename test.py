import pandas as pd
df = pd.read_csv(r'C:\Users\zxx91\Desktop\CARB-GEN-AI\data\Policy-Map-Ordinance-Table-May-2026.csv',
                 encoding='utf-8-sig', dtype=str, keep_default_na=False)
print(df['Exists? (Y/N)'].value_counts())
