import pandas as pd
import logging

logger = logging.getLogger(__name__)

def parse_contacts(file_path):
    try:
        df = pd.read_excel(file_path)
        
        if 'Phone' not in df.columns:
            return None, "Missing 'Phone' column in Excel file."
        
        def normalize_phone(p):
            p = str(p).strip().replace('+', '').replace('-', '').replace(' ', '')
            if p.endswith('.0'):
                p = p[:-2]
            if not p.isdigit():
                return None
            if len(p) == 10:
                p = '91' + p
            return p

        df['Phone'] = df['Phone'].apply(normalize_phone)
        df = df.dropna(subset=['Phone'])
        
        if 'Name' not in df.columns:
            df['Name'] = ''
        else:
            df['Name'] = df['Name'].fillna('').astype(str)
            
        contacts = df[['Name', 'Phone']].to_dict('records')
        return contacts, None
    except Exception as e:
        logger.error(f"Error parsing excel: {e}")
        return None, str(e)
