"""Flask route decorators fixture — read via ast.parse(), not imported."""
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/health")
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


@app.route("/users", methods=["GET", "POST"])
def users():
    """List or create users."""
    if request.method == "POST":
        data = request.get_json()
        return jsonify({"created": True, "data": data}), 201
    return jsonify({"users": []})


@app.route("/users/<int:user_id>", methods=["GET", "PUT", "DELETE"])
def user_detail(user_id):
    """Get, update, or delete a user."""
    return jsonify({"id": user_id})
