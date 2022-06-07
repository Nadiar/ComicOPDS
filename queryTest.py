import json


with open('test.json') as f:
    data = json.load(f)

for element in data:
    for key, value in element.items():
        title=key
        print("Search Title: " + title)
        query="SELECT * FROM COMICS where "
        for i in value:
            first=True
            for j,k in i.items():
                if k != '':
                    print(j,k)
                    if not first:
                        query = query + "and "
                    if type(k) == list:
                        print(k)
                        query = query + j + " in (" 
                        firstL = True
                        for l in k:
                            if not firstL: 
                                query = query + ","
                            query = query + "'" + l + "'"
                            if firstL:
                                firstL = False
                        query = query + ")"
                    else:
                        query = query + j + " like '%" + k + "%' "
                    if first:
                        first = False
        query = query + ";"
        print("----> " + query)
