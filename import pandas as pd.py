a=int(input("Enter the number of rows: "))
b=int(input("Enter the number of columns: "))   
data = []
for i in range(a):
    row = []
    for j in range(b):
        value = input(f"Enter value for row {i+1}, column {j+1}: ")
        row.append(value)
    data.append(row)        
df = pd.DataFrame(data)
print(df)

                