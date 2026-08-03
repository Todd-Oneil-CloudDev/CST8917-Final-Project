import json
import os
import aiohttp
import uuid
import azure.functions as func
import logging
from typing import Dict, Any
import datetime
from azure.durable_functions import OrchestrationRuntimeStatus
from azure.data.tables import TableServiceClient, ResourceExistsError

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# table storage
table_conn = os.getenv('TABLE_STORAGE_CONN')
table_name = os.getenv('TABLE_NAME')
service = TableServiceClient.from_connection_string(table_conn)
table_client = service.get_table_client(table_name)

# --- Helper Functions ---
def UpdatePayload(payload: Dict[str, Any], status: str):
    payload['Status'] = status

    return payload

def generate_id(): 
    return uuid.uuid4()

def update_audit_record(input: dict, status: str):
    input['Status'] = status
    input['ResolvedAt'] = datetime.fromisoformat(datetime.datetime.now(datetime.timezone.utc))

    table_client.upsert_entity(input)



# An HTTP-Triggered Function with a Durable Functions Client binding
@app.route(route="orchestrators/{functionName}")
@app.durable_client_input(client_name="client")
async def expense_orchestration_starter(req: func.HttpRequest, client):

    logging.info("expense-approval function triggered")

    try:
        expense = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}),
            mimetype="application/json",
            status_code=400
        )

    # Validate required fields
    required_fields = ["employeeName", "amount", "employeeEmail", "category", "description", "managerEmail"]
    missing = [field for field in required_fields if field not in expense]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Missing required fields: {', '.join(missing)}"}),
            mimetype="application/json",
            status_code=400
        )

    # Confirm amount is a number
    if not isinstance(expense['amount'], (int, float)):
        return func.HttpResponse(
            json.dumps({"error": f"{expense['amount']} is not a number type."}),
            mimetype="application/json",
            status_code=400
        )

    logging.info(f"processing expense...")

    function_name = req.route_params.get('functionName')
    instance_id = await client.start_new(
        function_name,
        client_input=expense)
    response = client.create_check_status_response(req, instance_id)

    return response


# Orchestrator
@app.orchestration_trigger(context_name="context")
def expense_orchestrator(context):

    skip_processing = False
    insert_error = False

    data = context.get_input()

    structure = {
        'PartitionKey': '',
        'RowKey': '',
        'ExpenseId': '',
        'EmployeeName': data['employeeName'],
        'EmployeeEmail': data['employeeEmail'],
        'Amount': data['amount'],
        'Category': data['category'],
        'Description': data['description'],
        'ManagerEmail': data['managerEmail'],
        'Status': '',
        'SubmittedAt': '',
        'ResolvedAt': ''
    }

    logging.info(f"Validating Category...")
    # --- Step 1: Validate ---
    validation_result = yield context.call_activity(
        "validate_category",
        data['category']
    )

    if not validation_result['is_valid']:
        logging.info(f"Category: {data['category']} Not Valid...")
        structure = UpdatePayload(
            structure, 
            'Rejected')
        skip_processing = True
    else:
        logging.info(f"Category Validated...")
        logging.info(f"Processing...")

        # --- Step 2: Insert Audit Record ---
        try:
            rec = yield context.call_activity(
                "insert_expense_record",
                structure
            )
        except Exception as e:
            insert_error = True
            skip_processing = True
            structure = UpdatePayload(
                structure, 
                'Failed To Insert')
            
        if structure['Status'] != 'Failed To Insert':
            # --- Step 3: Check Auto Approval ---
            if data['amount'] < 100:
                structure = UpdatePayload(
                    structure, 
                    'Approved')
                update_audit_record(structure, structure['Status'])
                skip_processing = True
                logging.info(f"Approved...")

    
    logging.info(f"Processing...")
    if not skip_processing:    

        # --- Step 4: Request Manager Approval ---
        approval_event = "manager_approval"
        timeout = context.current_utc_datetime + datetime.timedelta(seconds=60)

        # Creates a task to listen for a 'manager-approval' event triggered by a person
        context.set_custom_status ({'waiting_for': approval_event})
        approval_task = context.wait_for_external_event(approval_event)

        # Creates the timeout task setting the durable function timer to the defined 60 seconds above
        timeout_task = context.create_timer(timeout)

        logging.info(f"Awaiting Manager Approval...")

        # Races the 2 tasks defined above, which ever task responds first will decide processing logic
        winner = yield context.task_any([approval_task, timeout_task])

        # Clear custom status
        context.set_custom_status ({'waiting_for': None})

        if winner == timeout_task:
            logging.info(f"Manager Unavailable For Approval...")
            structure = UpdatePayload(
                structure, 
                'Escalated')
            update_audit_record(structure, structure['Status'])
        else:
            logging.info(f"Manager Responded...")
            manager_decision = approval_task.result
            if isinstance(manager_decision, str):
                manager_decision = json.loads(manager_decision)

            structure = UpdatePayload(
                structure, 
                manager_decision['decision'])
            update_audit_record(structure, structure['Status'])
                

    logging.info(f"Notifying...")
    # --- Step 5: Notify User ---
    notification_result = yield context.call_activity(
        "send_notification",
        {
            'ExpenseId': structure['ExpenseId'],
            'EmployeeName': structure['EmployeeName'],
            'EmployeeEmail': structure['EmployeeEmail'],
            'Amount': structure['Amount'],
            'Category': structure['Category'],
            'Description': structure['Description'],
            'ManagerEmail': structure['ManagerEmail'],
            'Status': structure['Status'],
            'SubmittedAt': structure['SubmittedAt'],
            'ResolvedAt': structure['ResolvedAt']
        }
    )

    sent = ''
    if notification_result == 200:
        sent = 'email sent'
    else:
        sent = 'email failed'

    return {
        "status": structure['Status'],
        "notification": sent 
    }


# Activity
# --- Send Notification ---
@app.activity_trigger(input_name="payload")
async def send_notification(payload: dict):
    logic_app_url = os.getenv('LOGIC_APP_NOTIFICATION_URL')

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(logic_app_url, json=payload) as response:
                response.raise_for_status()
                status = response.status
    except Exception as e:
        logging.error(f"Logic App call failed: {e}")
        return 500

    logging.info(f"Logic App call returned {status}")
    return status


# Activity
# --- Category Validiation ---
@app.activity_trigger(input_name="category")
async def validate_category(category: str):

    # Validate Category
    valid_categories = ["travel", "meals", "supplies", "equipment", "software", "other"]
    valid = category in valid_categories   

    return {
        'is_valid': valid,
        'reason': '' if valid else f"{category} is not a valid category"
    }


# Activity
#  --- Audit Table Storage ---
@app.activity_trigger(input_name="input_data")
async def insert_expense_record(input_data: dict):

    # generate ID
    id = generate_id()

    # generate Timestamp
    dt = datetime.fromisoformat(datetime.datetime.now(datetime.timezone.utc))
    partition_key = dt.strftime("%Y-%m")

    input_data['ExpenseId'] = id
    input_data['PartiionKey'] = partition_key
    input_data['RowKey'] = id
    input_data['Status'] = 'Pending'
    input_data['SubmittedAt'] = dt

    return table_client.create_entity(input_data)


# Activity
# --- Manager Approval Endpoint ---
@app.route(route="approval/{instance_id}")
@app.durable_client_input(client_name="client")
async def manager_approval(req: func.HttpRequest, client):
    """
    Manager approves or rejects an expense.
    Example:
      POST /approval/abc123
      body:{
        'decision': 'Approved',
      }
    """
    # Get orchestrator instance
    instance_id = req.route_params.get("instance_id")
    client_status = await client.get_status(instance_id)

    # check if orchestrator is running
    if client_status is None:
        return func.HttpResponse("Instance Not Found", status_code=404)

    print(type(client_status.runtime_status))

    if client_status.runtime_status not in [OrchestrationRuntimeStatus.Running, OrchestrationRuntimeStatus.Pending]:
        return func.HttpResponse("Instance Not Found", status_code=409)

    # Check custom status code
    waiting_for = None
    if client_status.custom_status and 'waiting_for' in client_status.custom_status:
        waiting_for = client_status.custom_status['waiting_for']

    if waiting_for != "manager_approval":
        return func.HttpResponse("Orchestrator not waiting for this event", status_code=409)

    try:
        approval = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}),
            mimetype="application/json",
            status_code=400
        )

    required_fields = ['decision']
    missing = [field for field in required_fields if field not in approval]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Missing required fields: {', '.join(missing)}"}),
            mimetype="application/json",
            status_code=400
        )

    allowed_dec = ['Approved', 'Rejected']
    allowed = approval['decision'] in allowed_dec 
    if not allowed:
        return func.HttpResponse(
            json.dumps({"error": f"{approval['decision']} is not a valid response. Must be either 'Approved' or 'Rejected'"}),
            mimetype="application/json",
            status_code=400
        )    

    await client.raise_event(instance_id, "manager_approval", approval)

    return func.HttpResponse(
        f"Sent decision '{approval}' to instance {instance_id}"
    )

# --- HEALTH CHECK ---
@app.function_name(name="health")
@app.route(route="", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Health check endpoint."""
    return func.HttpResponse(
        json.dumps({"status": "healthy", "service": "Expense Approval Pipeline"}),
        mimetype="application/json",
        status_code=200
    )