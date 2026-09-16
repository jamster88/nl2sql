## **Data Model Summary & Core Architecture**

This enterprise grocery data model uses an advanced **Star Schema** architecture tailored for high-volume retail analytics. It is optimized to solve typical grocery reporting challenges: **bulk and random-weight items** (e.g., produce or deli sold by fractions of a pound), **dual calendars** (decoupling financial reporting from marketing campaign schedules), and **cross-domain price execution indexing** (comparing internal pricing straight against external market rivals).

## ---

**1\. Conformed & Supporting Dimensions (Context Layers)**

These dimensions provide uniform, shared descriptive attributes. They serve as the definitive "glue" that allows data to be joined seamlessly across different transactional fact tables.

## **Table: dim\_date**

Tracks the standard corporate fiscal calendar, operational accounting timelines, and standard retail scheduling frameworks.

> * **date\_key** (INT, Primary Key): The unique identifier for each day formatted chronologically for partitioning and efficient joining (e.g., 20260915).  
> * **calendar\_date** (DATE, Unique): The actual calendar date value (e.g., 2026-09-15).  
> * **day\_of\_week\_name** (VARCHAR(9)): The full name of the day (e.g., "Tuesday"), useful for analyzing weekend checkout spikes versus mid-week lulls.  
> * **fiscal\_week\_num** (INT): The week number relative to the grocer’s financial year.  
> * **fiscal\_month\_num** (INT): The operational month number of the financial accounting year.  
> * **fiscal\_quarter** (INT): The corporate financial quarter (1 through 4).  
> * **fiscal\_year** (INT): The current active corporate reporting fiscal year.  
> * **nrf\_454\_week\_num** (INT): Intended as an NRF-style retail week number. In the generated data this column is populated with the same value as fiscal\_week\_num for every row, so it carries no independent information. The calendar itself follows a 4-4-5 week-per-month pattern (13 weeks per quarter, 52 per year).  
> * **is\_holiday** (BOOLEAN): Flag indicating official corporate or national holidays to adjust baseline sales forecasting parameters.

## **Table: dim\_promo\_calendar**

An independent, decoupled tracking timeline built specifically for the marketing department. It maps out floating thematic execution structures that do not cleanly align with strict fiscal calendar bounds.

> * **promo\_calendar\_key** (SERIAL, Primary Key): Auto-incrementing identifier for a specific marketing cycle block.  
> * **promo\_cycle\_id** (VARCHAR(50), Unique): The business identifier for the promotional rotation (e.g., PROMO\_2026\_WK32\_FALL).  
> * **promo\_cycle\_name** (VARCHAR(150)): Human-readable title of the promotional campaign push (e.g., "Back to School Blast Phase 1").  
> * **promo\_season\_type** (VARCHAR(50)): Categorization grouping for broad year-over-year trending analysis (e.g., "Summer Grilling", "Holiday Entertaining", "Lent").  
> * **cycle\_start\_date** (DATE): The date the promotional tag changes and circular prices go live on the store floor.  
> * **cycle\_end\_date** (DATE): The termination date for that structural promotional cycle block.  
> * **is\_major\_event\_cycle** (BOOLEAN): Flags high-impact events (e.g., Thanksgiving week or Black Friday) to distinguish them from standard weekly circular updates.

## **Table: dim\_product**

The absolute master index for all Stock Keeping Units (SKUs) across the grocery enterprise.

> * **product\_key** (SERIAL, Primary Key): Auto-incrementing identifier for internal reference optimization.  
> * **sku\_id** (VARCHAR(50), Unique): The baseline internal item corporate warehouse inventory code.  
> * **upc\_barcode** (VARCHAR(14)): Universal Product Code scanning value utilized at POS front-end registers.  
> * **product\_name** (VARCHAR(255)): Full descriptive item name including brand and line indicators.  
> * **brand\_name** (VARCHAR(100)): The brand label. All brands are fictional (e.g., "Timberline", "Ambervale"; private-label brands are "Everyday Basics", "Homestead Select", "Pantry Essentials", "ValueChoice").  
> * **department\_name** (VARCHAR(100)): Top-level store node hierarchy classification. 17 values, e.g. "Produce", "Dairy & Eggs", "Meat & Seafood", "Pantry & Canned Goods".  
> * **category\_name** (VARCHAR(100)): Mid-level organizational structure designation. 33 values, e.g. "Salty Snacks", "Soft Drinks", "Fresh Fruit".  
> * **sub\_category\_name** (VARCHAR(100)): Low-level item cluster assignment. 48 values, e.g. "Chips", "Carbonated Soda", "Apples".  
> * **package\_size\_desc** (VARCHAR(50)): Physical dimensions or weight capacity formatting notes. 15 values, e.g. "12 oz", "6-Pack", "Sold by the Lb". NULL for the 25 items whose product\_name already carries its own size or count.  
> * **is\_private\_label** (BOOLEAN): Flags items manufactured directly under the grocer's proprietary brand umbrella to monitor store brand penetration.

## **Table: dim\_store**

Captures properties, regional positions, and physical formats of bricks-and-mortar installations.

> * **store\_key** (SERIAL, Primary Key): Auto-incrementing identifier for relational joins.  
> * **store\_id** (VARCHAR(20), Unique): Real-world operational facility or brick-and-mortar location number.  
> * **store\_name** (VARCHAR(150)): The descriptive public name of the store branch location.  
> * **banner\_name** (VARCHAR(100)): The market banner brand variant under which this specific branch operates (e.g., "Premium Market" vs "Discount Warehouse").  
> * **street\_address, city, state\_code, postal\_code**: Geographic tracking markers used for spatial localization clusters.  
> * **square\_footage** (INT): Total interior building size, used as a baseline descriptor for shelf capacity and store performance tiering.  
> * **layout\_type\_desc** (VARCHAR(50)): Categorization of store design architecture (e.g., "Urban Compact", "Suburban Superstore").

## **Table: dim\_competitor**

Tracks rival retail operations mapped within the catchment area of your store network.

> * **competitor\_key** (SERIAL, Primary Key): Auto-incrementing identifier for indexing track logs.  
> * **competitor\_id** (VARCHAR(20), Unique): External identification code assigned to rival entities.  
> * **competitor\_name** (VARCHAR(150)): Parent corporate name of the competing grocery entity.  
> * **banner\_name** (VARCHAR(100)): Public trading brand banner of the rival location.  
> * **market\_positioning** (VARCHAR(50)): Strategic pricing archetype categorization. Exactly four values are in use: "Premium/Specialty", "Value/Discount", "Club/Warehouse", "Convenience/Small Format".

## **Table: dim\_promotion**

Maintains structural configuration rules and definitions for promotional discounts.

> * **promotion\_key** (SERIAL, Primary Key): Internal relational indexing value.  
> * **promotion\_id** (VARCHAR(50), Unique): Corporate promotional tracking identifier.  
> * **promotion\_name** (VARCHAR(150)): Campaign descriptor tag used on shelf labels and registers.  
> * **mechanic\_type** (VARCHAR(50)): Defines the underlying mathematical discount logic. Six values are in use: "BOGO", "Multi-Buy", "Mix-and-Match", "Loyalty Price Drop", "Percent Off", "Dollar Off".  
> * **min\_purchase\_requirement** (INT): The minimum item volume required to activate the discount logic rules.

## **Table: dim\_ad\_channel**

Defines media vectors utilized to transmit marketing campaigns to prospective consumers.

> * **ad\_channel\_key** (SERIAL, Primary Key): Auto-incrementing primary key identifier.  
> * **channel\_id** (VARCHAR(20), Unique): Operational media group designation tag.  
> * **channel\_type** (VARCHAR(50)): Baseline structural classification of media formats (e.g., "Print Flyer", "Digital Mailer", "Paid Social").  
> * **medium\_platform** (VARCHAR(100)): Specific logistical delivery system or vendor. Seven values are in use: "Sunday Paper Insert", "Weekly Circular", "Email Newsletter", "Instagram", "Facebook", "Mobile App", "Homepage".

## **Table: dim\_ad\_placement**

The structural descriptive repository for physical or digital advertising assets, decoupled from performance logs.

> * **ad\_placement\_key** (SERIAL, Primary Key): Relational identifier link.  
> * **ad\_id** (VARCHAR(50), Unique): Master content code reference identifier.  
> * **ad\_channel\_key** (INT, Foreign Key): Relational link tracing back straight into dim\_ad\_channel.  
> * **ad\_theme\_name** (VARCHAR(150)): Creative conceptual category label (e.g., "Labor Day Ribeye Extravaganza").  
> * **creative\_version\_code** (VARCHAR(50)): Code version tag utilized to isolate variants for A/B testing frameworks.  
> * **page\_number\_slot** (VARCHAR(20)): Physical location allocation identifier. Ten values are in use, e.g. "Page 1 \- Top Left", "Center Spread", "Mobile Banner 1", "Homepage Hero".  
> * **is\_front\_page\_feature** (BOOLEAN): Identifies premier visual advertisements, used to measure downstream consumer traffic lift.

## **Table: dim\_vendor**

Tracks suppliers providing inventory assets to the grocery warehouse systems.

> * **vendor\_key** (SERIAL, Primary Key): Primary identifier.  
> * **vendor\_id** (VARCHAR(50), Unique): Master corporate vendor account ledger identity flag.  
> * **vendor\_name** (VARCHAR(150)): Registered operational trading name of the supplier.  
> * **payment\_terms\_desc** (VARCHAR(50)): Contractual terms detailing payment cycles. Five values are in use: "Net 30", "Net 45", "Net 60", "2/10 Net 30", "1/15 Net 45".

## **Table: dim\_allowance\_type**

Categorizes rebate codes used to track funding support programs received from suppliers.

> * **allowance\_type\_key** (SERIAL, Primary Key): Relational tracking primary link.  
> * **allowance\_type\_code** (VARCHAR(30), Unique): Programming key for vendor billing software systems. Seven codes exist: SCAN\_BACK, SLOTTING, SPOILAGE, VOLUME\_REBATE, ADVERTISING, NEW\_ITEM, DISPLAY.  
> * **allowance\_type\_name** (VARCHAR(100)): Full descriptive name of the vendor trade funding mechanism.

## **Table: dim\_geography**

Structural regional tracking nodes for syndicated external competitive market data assets.

> * **market\_region\_key** (SERIAL, Primary Key): Internal structural link identity key.  
> * **region\_id** (VARCHAR(50), Unique): Boundary identification key.  
> * **region\_name** (VARCHAR(100)): Descriptive geographical boundary assignment (e.g., "Pacific Northwest").  
> * **syndicated\_market\_code** (VARCHAR(50)): Synthetic DMA-style codes in the form DMA-nnn (e.g., DMA-335). They do not correspond to any real syndicator’s numbering.

## ---

**2\. Sales and Cost Domain (Operational Core)**

Tracks POS receipt line items, inventory procurement values, and supplier funding programs.

## **Table: fact\_pos\_retail\_sales**

Captures granular register transaction loops.

> * **Composite Primary Key:** (sales\_date\_key, product\_key, store\_key, basket\_id)  
> * **sales\_date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store  
> * **basket\_id** (VARCHAR(64)) *(Degenerate Dimension)*: The unique receipt ticket ID transaction string.  
> * **quantity\_sold** (NUMERIC(12,3)): The volume of units checked out. The 3-decimal precision natively supports variable-weight bulk inventory classifications (e.g., 2.345 pounds of organic bananas).  
> * **gross\_sales\_amt** (NUMERIC(12,2)): Baseline transaction value calculated as quantity\_sold × standard baseline retail shelf price.  
> * **markdown\_discount\_amt** (NUMERIC(12,2)): Total savings subtracted from the transaction value via loyalty markdown sweeps or localized manager specials.  
> * **net\_sales\_amt** (NUMERIC(12,2)): The actual cash collected at the register (gross\_sales\_amt \- markdown\_discount\_amt).

## **Table: fact\_item\_cogs**

Tracks localized product cost components, isolated from front-end checkout traffic. Despite the daily-grain primary key, rows are written at a **monthly** grain: 24 date\_key values, one per fiscal month, each the first day of that fiscal month.

> * **Composite Primary Key:** (date\_key, product\_key, store\_key)  
> * **date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store  
> * **base\_cost** (NUMERIC(12,4)): Net invoice unit price charged by the supplier. High precision prevents rounding errors when scaling up from tiny per-ounce costs.  
> * **freight\_cost** (NUMERIC(12,4)): Internal logistics, fuel, and distribution center cross-docking overhead allocated per unit.  
> * **net\_item\_cost** (NUMERIC(12,4)): The definitive final cost structure value (base\_cost \+ freight\_cost) used to evaluate pure gross margin profitability performance. Each component is rounded independently, so the sum can differ from this column by up to 0.0001.

## **Table: fact\_vendor\_allowances**

Logs trade funding adjustments where suppliers credit the grocer based on promotional sales volumes or setup fees.

> * **Composite Primary Key:** (date\_key, product\_key, store\_key, vendor\_key, allowance\_type\_key)  
> * **date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store  
> * **vendor\_key** (INT, Foreign Key) $\\rightarrow$ dim\_vendor  
> * **allowance\_type\_key** (INT, Foreign Key) $\\rightarrow$ dim\_allowance\_type  
> * **allowance\_rate\_per\_unit** (NUMERIC(12,4)): The contractually agreed funding rate reimbursed per unit sold or scanned (e.g., $0.50 back per box).  
> * **total\_allowance\_amt** (NUMERIC(12,2)): The total accrued financial credit earned during the tracking interval, including flat-rate slotting bonuses.

## ---

**3\. Shared Pricing Layer (Analytical Cross-Domain Bridge)**

## **Table: fact\_item\_prices**

A unified reference table that maps pricing tiers for every stocked SKU at every retail location. Despite the daily-grain primary key, rows are written **weekly**: 104 date\_key values, each the first day of a fiscal week, and a price holds for the remainder of that week. It serves as the master baseline data layer for calculating price elasticity and identifying margin compression across all domains.

> * **Composite Primary Key:** (date\_key, product\_key, store\_key)  
> * **date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store  
> * **regular\_retail\_price** (NUMERIC(12,2)): Standard, non-promotional shelf tag retail base price.  
> * **base\_promo\_price** (NUMERIC(12,2)): The corporate-approved promotional markdown price. This column holds a NULL value if the item is selling at its standard base rate on that day.

## ---

**4\. Competition and Market Share Domain**

Integrates external competitive intelligence data sets with targeted localized retail field observations.

## **Table: fact\_competitor\_pricing**

Tracks local competitor prices collected via automated web scrapers or field data verification teams.

> * **Composite Primary Key:** (date\_key, product\_key, competitor\_key, store\_key)  
> * **date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **competitor\_key** (INT, Foreign Key) $\\rightarrow$ dim\_competitor  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store: The matching internal anchor store location used to define the competitive radius.  
> * **comp\_regular\_price** (NUMERIC(12,2)): Observed standard shelf tag retail price at the rival location.  
> * **comp\_promo\_price** (NUMERIC(12,2)): Observed active promotional flyer feature price at the rival location.

## **Table: fact\_market\_share\_weekly**

Models the broad, regional market data a grocer would buy from an external syndicator, used to track market share performance against key rivals. The figures here are synthetic and come from no real syndicator. Grain is weekly: 104 week\_key values, each the first day of a fiscal week, covering 8 regions x 70 tracked products x 5 competitors.

> * **Composite Primary Key:** (week\_key, product\_key, market\_region\_key, competitor\_key)  
> * **week\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date: Maps to the **first** day of the targeted fiscal week, not its concluding date (verified for all 104 week\_key values present).  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **market\_region\_key** (INT, Foreign Key) $\\rightarrow$ dim\_geography  
> * **competitor\_key** (INT, Foreign Key) $\\rightarrow$ dim\_competitor  
> * **grocer\_sales\_amount** (NUMERIC(15,2)): Total sales volume generated by your own brand network within that regional boundary.  
> * **competitor\_sales\_amount** (NUMERIC(15,2)): Estimated revenue captured by the rival banner within the same regional market.  
> * **total\_market\_sales\_amount** (NUMERIC(15,2)): Total consumer market expenditure logged across all retailers within the tracked geographical boundary. **Fan-out warning:** each (week\_key, product\_key, market\_region\_key) cell has exactly five rows, one per competitor, and both this column and grocer\_sales\_amount repeat identically on all five. Summing either column without de-duplicating multiplies the true value by five.

## ---

**5\. Promotions and Ads Domain (Marketing Matrix)**

Tracks marketing campaign execution performance across the dual calendar frameworks.

## **Table: fact\_promo\_performance**

Measures volume changes and incremental sales lift generated by active promotional campaigns.

> * **Composite Primary Key:** (date\_key, promo\_calendar\_key, product\_key, store\_key, promotion\_key)  
> * **date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date *(Corporate Fiscal tracking point link)*  
> * **promo\_calendar\_key** (INT, Foreign Key) $\\rightarrow$ dim\_promo\_calendar *(Marketing Campaign tracking cycle link)*  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store  
> * **promotion\_key** (INT, Foreign Key) $\\rightarrow$ dim\_promotion  
> * **promo\_quantity\_sold** (NUMERIC(12,3)): Total item volume moved through checkout points while using this specific promotional structure code.  
> * **promo\_quantity\_lift** (NUMERIC(12,3)): The calculated incremental volume variance, determined by subtracting standard non-promotional baseline sales forecasts from actual promotional sales volumes. In the generated data this is always positive (0.495 to 43.514); no negative lift is present.

## **Table: fact\_ad\_performance**

Tracks variable performance metrics for physical and digital advertisement placements, isolated from static campaign creative details.

> * **Composite Primary Key:** (date\_key, ad\_placement\_key, product\_key, store\_key)  
> * **date\_key** (INT, Foreign Key) $\\rightarrow$ dim\_date  
> * **ad\_placement\_key** (INT, Foreign Key) $\\rightarrow$ dim\_ad\_placement  
> * **product\_key** (INT, Foreign Key) $\\rightarrow$ dim\_product  
> * **store\_key** (INT, Foreign Key) $\\rightarrow$ dim\_store  
> * **ad\_spend\_amount** (NUMERIC(12,2)): Localized, daily budget allocation spent on this specific advertising vector.  
> * **impressions\_count** (INT): The total volume of visual views logged across print distribution lines or digital screen loads.  
> * **clicks\_or\_coupon\_clips\_count** (INT): User interaction metric logging digital link clicks or mobile loyalty coupon clipping actions.