import json, random, sys
mode = sys.argv[1]; n = int(sys.argv[2]); out = sys.argv[3]
random.seed(42)
price = 375000.0
with open(out,"w") as f:
    for i in range(n):
        if mode == "uniform":
            d = random.randrange(10)
        elif mode == "biased":          # digit 7 at 11.5%, rest share remainder
            d = 7 if random.random() < 0.115 else random.choice([x for x in range(10) if x!=7])
        elif mode == "markov":          # next digit repeats previous 13% of time
            prev = d if i else random.randrange(10)
            d = prev if random.random() < 0.13 else random.randrange(10)
        price += random.gauss(0, 20)
        q = round(int(price) + d/10000.0 + random.randrange(1000)/10.0, 4)
        q = float(f"{int(price)}.{random.randrange(1000):03d}{d}")
        f.write(json.dumps({"epoch": 1690000000 + 2*i, "quote": q, "digit": d})+"\n")
