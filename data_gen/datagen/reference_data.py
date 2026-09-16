"""Static, fictional reference content used to make the synthetic rows read
like a real grocery chain without naming any real company, brand, or place.

Everything here is hand-authored lookup content (category trees, name
templates, banner names, ...), not randomized -- randomization happens in
dimensions.py / facts.py using these tables as the raw material.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Product hierarchy: department -> category -> { subcategory: (base item
# names, unit_kind, (low, high) regular price range in dollars) }
# unit_kind is "each" (packaged/count items, quantity_sold is ~always 1) or
# "weight" (random-weight items like produce/meat/bakery/deli, quantity_sold
# is a fractional lb amount).
# ---------------------------------------------------------------------------

PRODUCT_HIERARCHY = {
    "Produce": {
        "Fresh Fruit": {
            "Apples": (["Gala Apples", "Honeycrisp Apples", "Granny Smith Apples"], "weight", (1.49, 3.49)),
            "Bananas": (["Bananas"], "weight", (0.49, 0.79)),
            "Berries": (["Strawberries", "Blueberries", "Raspberries"], "each", (2.99, 6.99)),
            "Citrus": (["Navel Oranges", "Lemons", "Limes"], "weight", (0.99, 2.99)),
        },
        "Fresh Vegetables": {
            "Leafy Greens": (["Romaine Lettuce", "Baby Spinach", "Kale"], "each", (1.99, 4.49)),
            "Root Vegetables": (["Russet Potatoes", "Carrots", "Yellow Onions"], "weight", (0.79, 2.49)),
            "Tomatoes & Peppers": (["Roma Tomatoes", "Bell Peppers", "Jalapenos"], "weight", (1.29, 3.99)),
        },
    },
    "Dairy & Eggs": {
        "Milk": {
            "Fluid Milk": (["Whole Milk", "2% Reduced Fat Milk", "Skim Milk"], "each", (2.79, 4.99)),
        },
        "Cheese": {
            "Shredded Cheese": (["Shredded Cheddar", "Shredded Mozzarella", "Mexican Blend Cheese"], "each", (3.49, 6.49)),
            "Block & Sliced Cheese": (["Colby Jack Block", "Swiss Slices", "Provolone Slices"], "each", (3.99, 7.99)),
        },
        "Eggs & Yogurt": {
            "Eggs": (["Grade A Large Eggs", "Cage Free Eggs", "Organic Eggs"], "each", (2.49, 5.99)),
            "Yogurt": (["Greek Yogurt Cup", "Vanilla Yogurt Cup", "Yogurt Multipack"], "each", (0.99, 5.99)),
        },
    },
    "Meat & Seafood": {
        "Beef": {
            "Ground Beef": (["80/20 Ground Beef", "90/10 Ground Beef"], "weight", (4.99, 8.99)),
            "Steak": (["Ribeye Steak", "Sirloin Steak", "T-Bone Steak"], "weight", (8.99, 17.99)),
        },
        "Poultry": {
            "Chicken": (["Boneless Chicken Breast", "Chicken Thighs", "Whole Chicken"], "weight", (2.49, 6.99)),
        },
        "Seafood": {
            "Fresh Seafood": (["Atlantic Salmon Fillet", "Shrimp", "Tilapia Fillet"], "weight", (6.99, 15.99)),
        },
    },
    "Bakery": {
        "Bread": {
            "Sandwich Bread": (["White Sandwich Bread", "Whole Wheat Bread", "Multigrain Bread"], "each", (2.49, 4.99)),
            "Artisan Bread": (["Sourdough Boule", "French Baguette", "Ciabatta Loaf"], "each", (3.49, 5.99)),
        },
        "Sweet Baked Goods": {
            "Cakes & Muffins": (["Chocolate Chip Muffins", "Birthday Cake Slice", "Cupcake 4-Pack"], "each", (3.99, 12.99)),
        },
    },
    "Deli": {
        "Prepared Foods": {
            "Sliced Meats": (["Deli Ham", "Deli Turkey", "Rotisserie Chicken"], "weight", (5.99, 9.99)),
            "Salads": (["Potato Salad", "Coleslaw", "Macaroni Salad"], "weight", (3.99, 6.99)),
        },
    },
    "Frozen Foods": {
        "Frozen Meals": {
            "Entrees": (["Frozen Lasagna", "Chicken Pot Pie", "Stir Fry Bowl"], "each", (3.99, 7.99)),
        },
        "Frozen Treats": {
            "Ice Cream": (["Vanilla Ice Cream", "Chocolate Ice Cream", "Cookies and Cream Ice Cream"], "each", (3.99, 6.99)),
        },
        "Frozen Vegetables": {
            "Vegetable Blends": (["Frozen Broccoli", "Frozen Mixed Vegetables", "Frozen Corn"], "each", (1.99, 3.99)),
        },
    },
    "Beverages": {
        "Soft Drinks": {
            "Carbonated Soda": (["Cola 12-Pack", "Lemon Lime Soda 12-Pack", "Root Beer 12-Pack"], "each", (4.99, 8.99)),
        },
        "Water & Sports Drinks": {
            "Bottled Water": (["Spring Water 24-Pack", "Sparkling Water 8-Pack"], "each", (3.99, 7.99)),
        },
        "Coffee & Tea": {
            "Ground Coffee": (["Ground Coffee 12oz", "Coffee K-Cup 12-Pack"], "each", (5.99, 11.99)),
        },
    },
    "Snacks & Candy": {
        "Salty Snacks": {
            "Chips": (["Potato Chips", "Tortilla Chips", "Pretzels"], "each", (2.99, 5.99)),
        },
        "Sweet Snacks": {
            "Cookies & Candy": (["Chocolate Chip Cookies", "Chocolate Candy Bag", "Gummy Candy"], "each", (2.49, 5.99)),
        },
    },
    "Breakfast & Cereal": {
        "Cereal": {
            "Cold Cereal": (["Corn Flakes", "Honey Nut Cereal", "Oat Rings Cereal"], "each", (3.49, 6.49)),
        },
        "Breakfast Foods": {
            "Oatmeal & Bars": (["Instant Oatmeal 10-Pack", "Granola Bars 8-Pack"], "each", (2.99, 5.99)),
        },
    },
    "Pantry & Canned Goods": {
        "Pasta & Rice": {
            "Dry Pasta": (["Spaghetti", "Penne Pasta", "Elbow Macaroni"], "each", (1.29, 2.99)),
            "Rice & Grains": (["Long Grain White Rice", "Jasmine Rice", "Quinoa"], "each", (2.49, 6.99)),
        },
        "Canned Goods": {
            "Canned Vegetables": (["Canned Corn", "Canned Green Beans", "Diced Tomatoes"], "each", (0.89, 2.29)),
            "Soup": (["Chicken Noodle Soup", "Tomato Soup", "Vegetable Soup"], "each", (1.49, 3.49)),
        },
    },
    "Condiments & Sauces": {
        "Condiments": {
            "Table Condiments": (["Ketchup", "Yellow Mustard", "Mayonnaise"], "each", (2.49, 5.99)),
        },
        "Sauces": {
            "Pasta Sauce": (["Marinara Sauce", "Alfredo Sauce"], "each", (2.49, 4.99)),
            "Salad Dressing": (["Ranch Dressing", "Italian Dressing"], "each", (2.99, 5.49)),
        },
    },
    "Baking Supplies": {
        "Baking Basics": {
            "Flour & Sugar": (["All-Purpose Flour", "Granulated Sugar", "Brown Sugar"], "each", (1.99, 4.49)),
            "Mixes": (["Chocolate Cake Mix", "Pancake Mix"], "each", (1.99, 3.99)),
        },
    },
    "Health & Beauty": {
        "Oral & Personal Care": {
            "Oral Care": (["Toothpaste", "Toothbrush 2-Pack", "Mouthwash"], "each", (2.99, 6.99)),
            "Hair Care": (["Shampoo", "Conditioner"], "each", (3.99, 9.99)),
        },
        "Vitamins": {
            "Supplements": (["Multivitamin 60ct", "Vitamin C Gummies"], "each", (6.99, 16.99)),
        },
    },
    "Household & Cleaning": {
        "Paper Goods": {
            "Paper Products": (["Paper Towels 6-Roll", "Bath Tissue 12-Roll", "Facial Tissue"], "each", (4.99, 14.99)),
        },
        "Cleaning Supplies": {
            "Cleaners": (["All-Purpose Cleaner", "Dish Soap", "Laundry Detergent"], "each", (2.99, 12.99)),
        },
    },
    "Baby & Child": {
        "Baby Care": {
            "Diapering": (["Diapers Size 3 Box", "Baby Wipes 3-Pack"], "each", (9.99, 24.99)),
            "Feeding": (["Infant Formula", "Baby Food Pouch 4-Pack"], "each", (3.99, 22.99)),
        },
    },
    "Pet Care": {
        "Pet Food": {
            "Dog Food": (["Dry Dog Food", "Wet Dog Food Can"], "each", (1.49, 24.99)),
            "Cat Food": (["Dry Cat Food", "Wet Cat Food Can"], "each", (1.29, 19.99)),
        },
    },
    "Floral & Garden": {
        "Floral": {
            "Bouquets & Plants": (["Fresh Cut Bouquet", "Potted Succulent", "Seasonal Plant"], "each", (7.99, 24.99)),
        },
    },
}

NATIONAL_BRANDS = [
    "Harvest & Home", "Sunridge Farms", "Golden Fields", "Blue Ridge Kitchens",
    "Meadowbrook", "Northgate Provisions", "Cascade Valley", "Silver Creek Co.",
    "Rolling Hills", "Cedarwood Kitchens", "Ambervale", "Bright Orchard",
    "Old Mill Co.", "Pinecrest Pantry", "Wildflower Kitchens", "Copper Kettle",
    "Fairview Foods", "Timberline", "Sweetwater Provisions", "Redstone Kitchens",
]

PRIVATE_LABEL_BRANDS = [
    "ValueChoice", "Everyday Basics", "Homestead Select", "Pantry Essentials",
]

# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

STORE_BANNERS = {
    "Green Acre Market": ("Traditional Supermarket", (38000, 55000)),
    "Corner Fresh Grocers": ("Neighborhood Market", (18000, 30000)),
    "Thrift & Table": ("Value/Discount", (25000, 40000)),
    "Heritage Provisions Co-op": ("Traditional Supermarket", (35000, 50000)),
    "Metro Express Foods": ("Urban Small Format", (8000, 16000)),
}

US_STATE_CITIES = [
    ("Springfield", "IL"), ("Franklin", "TN"), ("Georgetown", "TX"),
    ("Clinton", "OH"), ("Madison", "WI"), ("Salem", "OR"),
    ("Fairview", "NC"), ("Greenville", "SC"), ("Bristol", "CT"),
    ("Arlington", "VA"), ("Riverside", "CA"), ("Kingston", "NY"),
    ("Ashland", "KY"), ("Manchester", "NH"), ("Lakewood", "CO"),
    ("Newport", "RI"), ("Auburn", "AL"), ("Jackson", "MS"),
    ("Marion", "IN"), ("Concord", "NC"),
]

# ---------------------------------------------------------------------------
# Competitors
# ---------------------------------------------------------------------------

COMPETITORS = [
    ("Heritage Fine Foods", "Premium/Specialty"),
    ("PennyWise Grocers", "Hard Discount"),
    ("Metro Mart", "Mainstream Conventional"),
    ("Bulk Barn Wholesale Club", "Club/Warehouse"),
    ("ValuMax Foods", "Value/Discount"),
    ("QuickStop Grocery", "Convenience/Small Format"),
    ("Countryside Markets", "Mainstream Conventional"),
    ("Foothill Fresh", "Premium/Specialty"),
]

# ---------------------------------------------------------------------------
# Geography / market regions
# ---------------------------------------------------------------------------

MARKET_REGIONS = [
    "Pacific Northwest", "Southwest Desert", "Upper Midwest", "Great Lakes",
    "Mid-Atlantic", "Southeast Coastal", "Gulf Coast", "Mountain West",
    "New England", "Tristate Metro", "Central Plains", "Piedmont",
]

# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------

PROMO_MECHANICS = [
    ("BOGO", 2), ("Mix-and-Match", 3), ("Loyalty Price Drop", 1),
    ("Percent Off", 1), ("Dollar Off", 1), ("Multi-Buy", 4),
]

SEASON_BY_MONTH = {
    1: "New Year New You", 2: "Big Game & Valentine's", 3: "Spring Cleaning",
    4: "Easter & Spring Refresh", 5: "Memorial Day Kickoff", 6: "Summer Grilling",
    7: "Summer Grilling", 8: "Back to School", 9: "Labor Day Savings",
    10: "Fall Harvest & Halloween", 11: "Thanksgiving Feast", 12: "Holiday Season",
}

MAJOR_SEASONS = {
    "Thanksgiving Feast", "Holiday Season", "Back to School",
    "Summer Grilling", "Big Game & Valentine's",
}

# ---------------------------------------------------------------------------
# Advertising
# ---------------------------------------------------------------------------

AD_CHANNELS = [
    ("Print Flyer", "Sunday Paper Insert"),
    ("Print Flyer", "Weekly Circular"),
    ("Digital Mailer", "Email Newsletter"),
    ("Paid Social", "Instagram"),
    ("Paid Social", "Facebook"),
    ("In-App Push", "Mobile App"),
    ("Website Banner", "Homepage"),
    ("In-Store Display", "End Cap Display"),
    ("Search", "Search Ads"),
]

AD_PAGE_SLOTS_PRINT = ["Page 1 - Top Left", "Page 1 - Full", "Page 2 - Top Right", "Back Page", "Center Spread"]
AD_PAGE_SLOTS_DIGITAL = ["Mobile Banner 1", "Homepage Hero", "Email Header", "In-Feed Card 1", "Search Result Top"]

# ---------------------------------------------------------------------------
# Vendors & allowances
# ---------------------------------------------------------------------------

VENDOR_SUFFIXES = ["Foods", "Brands", "Distributors", "Provisions Co.", "Supply Co.", "Group"]

PAYMENT_TERMS = ["Net 30", "Net 45", "Net 60", "2/10 Net 30", "1/15 Net 45"]

ALLOWANCE_TYPES = [
    ("SCAN_BACK", "Scan-Back Allowance"),
    ("SLOTTING", "Slotting Fee"),
    ("SPOILAGE", "Spoilage Allowance"),
    ("VOLUME_REBATE", "Volume Rebate"),
    ("ADVERTISING", "Cooperative Advertising Allowance"),
    ("NEW_ITEM", "New Item Introduction Allowance"),
    ("DISPLAY", "Display Allowance"),
]
