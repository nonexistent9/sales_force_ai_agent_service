import json
from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import asyncio
from tabulate import tabulate
load_dotenv()

def login_with_user_pass_token() -> Salesforce:
    """
    Auth using username + password + security token via simple-salesforce.
    Requires .env: SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN, SF_DOMAIN
    """
    user = os.getenv("SF_USERNAME")
    pwd = os.getenv("SF_PASSWORD")
    token = os.getenv("SF_SECURITY_TOKEN")
    domain = os.getenv("SF_DOMAIN", "login")

    if not all([user, pwd, token]):
        raise RuntimeError("Missing SF_USERNAME/SF_PASSWORD/SF_SECURITY_TOKEN in environment")

    return Salesforce(username=user, password=pwd, security_token=token, domain=domain)

# assuming you already authenticated:
sf = login_with_user_pass_token()   # or login_with_oauth_password_grant()


async def async_query_salesforce(soql: str):
    try:
        #results = await sf.query(soql)
        results = await asyncio.to_thread(sf.query, soql)
        return results
    except Exception as e:
        print(f"Error querying Salesforce: {e}")
        return {"error": str(e)}



# Query contacts where LastName = 'Doe'
#results = sf.query("SELECT Id, FirstName, LastName, Email, Account.Name FROM Contact WHERE LastName = 'Doe'")

async def get_sf_object_info(object_name: str):
    try:
        desc = await asyncio.to_thread(sf.__getattr__(object_name).describe)
        desc = sf.Contact.describe()
        fields = desc["fields"]

        # System/readonly fields to always skip (extend as you like)
        SKIP_NAMES = {
            "IsDeleted","MasterRecordId","CreatedDate","CreatedById","LastModifiedDate",
            "LastModifiedById","SystemModstamp","LastActivityDate","LastViewedDate",
            "LastReferencedDate","PhotoUrl","IsEmailBounced","LastCURequestDate","LastCUUpdateDate",
            "Jigsaw","JigsawContactId","Name"  # "Name" on Contact is a calculated Full Name
        }
        # Field types to skip for CSV/data loads (compound/derived)
        SKIP_TYPES = {"address","location","anyType"}

        def is_useful_for_insert(f):
            # keep only createable, non-deprecated, not compound/systemy
            if not f.get("createable", False):
                return False
            if f.get("deprecatedAndHidden", False):
                return False
            if f["name"] in SKIP_NAMES:
                return False
            if f["type"] in SKIP_TYPES:
                return False
            return True

        useful = [
            {
                #"Id": f["id"],
                "name": f["name"],
                "label": f["label"],
                "type": f["type"],
            #    "length": f.get("length"),
            #    "required": not f.get("nillable", True),
            #    "updateable": f.get("updateable", False),
            #    "custom": f.get("custom", False),
            }
            for f in fields
            if is_useful_for_insert(f)
        ]

        return tabulate(useful)
        #try:
        #    from tabulate import tabulate  # pip install tabulate
        #    print(tabulate(
        #        useful,
        #        headers={"name":"name","label":"label","type":"type","length":"length","required":"required","updateable":"updateable","custom":"custom"},
        #        tablefmt="github"
        #    ))
        #    return useful
        #except Exception:
        #    for f in useful:
        #        print(f)
        #    return useful
    except Exception as e:
        print(f"Error retrieving Salesforce object info: {e}")
        return {"error": str(e)}


#useful = asyncio.run(get_sf_object_info("Opportunity"))

#print(tabulate(useful))

#for uf in useful_fields:
#    print(uf)

#contact_schema = sf.Contact.describe()
#account_schema = sf.Account.describe()
#opportunity_schema = sf.Opportunity.describe()

#print(json.dumps(contact_schema, indent=2))

#results = asyncio.run(async_query_salesforce("SELECT Id, FirstName, LastName, Email, Account.Name FROM Contact WHERE LastName = 'Doe'"))

#for rec in results['records']:
#    print(f"{rec['FirstName']} {rec['LastName']} | Email: {rec.get('Email')} | Account: {rec.get('Account', {}).get('Name')}")
