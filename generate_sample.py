import pandas as pd

def generate_sample():
    data = {
        'Name': ['Rahul', 'Amit', 'Priya', 'John Doe'],
        'Phone': ['9876543210', '919876543211', '+91-9876543212', '987654321X'] 
    }
    df = pd.DataFrame(data)
    df.to_excel('sample_contacts.xlsx', index=False)
    print("Generated sample_contacts.xlsx")

if __name__ == '__main__':
    generate_sample()
