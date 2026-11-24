import json
import os
import random
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.exceptions import BadGateway, NotFound

app = Flask(__name__)
app.config["PERSONAL_RECIPES"] = Path(__file__).with_name("personal_recipes.json")
MEALDB = "https://www.themealdb.com/api/json/v1/1"
recipes_lock = Lock()


def fetch_meals(endpoint, value):
    try:
        response = requests.get(f"{MEALDB}/{endpoint}.php", params={"i": value}, timeout=10)
        response.raise_for_status()
        return response.json().get("meals") or []
    except (requests.RequestException, ValueError):
        abort(502, description="TheMealDB is unavailable.")


def get_recipe_for(main_ingredient: str) -> dict:
    ingredient = " ".join(main_ingredient.replace("_", " ").lower().split())
    matches = fetch_meals("filter", ingredient.replace(" ", "_"))
    if not matches:
        abort(404, description="No recipes found for this ingredient.")

    meals = fetch_meals("lookup", random.choice(matches)["idMeal"])
    if not meals:
        abort(404, description="Recipe details were not found.")

    meal = meals[0]
    ingredients = {}
    for number in range(1, 21):
        name = (meal.get(f"strIngredient{number}") or "").strip()
        if name:
            ingredients[name] = (meal.get(f"strMeasure{number}") or "").strip()

    return {
        "name": meal["strMeal"],
        "image": meal.get("strMealThumb") or "",
        "video": meal.get("strYoutube") or "",
        "instructions": meal.get("strInstructions") or "",
        "ingredients": ingredients,
    }


def load_personal_recipes():
    path = Path(app.config["PERSONAL_RECIPES"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def save_personal_recipes(recipes):
    path = Path(app.config["PERSONAL_RECIPES"])
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary_path = Path(stream.name)
        try:
            json.dump(recipes, stream, ensure_ascii=False, indent=2)
        except Exception:
            stream.close()
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def find_recipe(ingredient):
    wanted = " ".join(ingredient.replace("_", " ").casefold().split())
    matches = [
        recipe for recipe in load_personal_recipes()
        if any(" ".join(name.casefold().split()) == wanted for name in recipe["ingredients"])
    ]
    if not matches:
        return get_recipe_for(ingredient)
    try:
        matches.append(get_recipe_for(ingredient))
    except (NotFound, BadGateway):
        pass
    return random.choice(matches)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/recipe/<ingredient>")
def recipe(ingredient):
    return render_template("recipe.html", ingredient=ingredient, recipe=find_recipe(ingredient))


@app.route("/recipe/<ingredient>/json")
def recipe_json(ingredient):
    return jsonify(find_recipe(ingredient))


@app.post("/personal")
def add_personal_recipe():
    recipe_data = request.get_json(silent=True)
    if not isinstance(recipe_data, dict):
        abort(400, description="Expected a recipe JSON object.")
    name = recipe_data.get("name")
    ingredients = recipe_data.get("ingredients")
    if not isinstance(name, str) or not name.strip() or "/" in name:
        abort(400, description="Expected a recipe name without slashes.")
    if not isinstance(ingredients, dict) or not all(
        key.strip() and isinstance(value, str) for key, value in ingredients.items()
    ):
        abort(400, description="Expected an ingredients object with amounts as strings.")

    saved = {"name": name.strip(), "ingredients": ingredients}
    for field in ("image", "video", "instructions"):
        value = recipe_data.get(field, "")
        if not isinstance(value, str):
            abort(400, description=f"Expected a string for {field}.")
        if field in ("image", "video") and value and not value.startswith(("https://", "http://")):
            abort(400, description=f"Expected an HTTP or HTTPS URL for {field}.")
        saved[field] = value

    with recipes_lock:
        recipes = load_personal_recipes()
        if any(entry["name"] == saved["name"] for entry in recipes):
            abort(409, description="A recipe with that name already exists.")
        recipes.append(saved)
        save_personal_recipes(recipes)
    return redirect(url_for("personal_recipe", name=saved["name"], json=0), code=303)


@app.get("/personal/<name>/<int:json>")
def personal_recipe(name, json):
    if json not in (0, 1):
        abort(404)
    saved = next((entry for entry in load_personal_recipes() if entry["name"] == name), None)
    if saved is None:
        abort(404)
    if json == 1:
        return jsonify(saved)
    return render_template("recipe.html", ingredient=None, recipe=saved)


@app.delete("/personal/<name>")
def delete_personal_recipe(name):
    with recipes_lock:
        recipes = load_personal_recipes()
        remaining = [entry for entry in recipes if entry["name"] != name]
        if len(remaining) == len(recipes):
            abort(404)
        save_personal_recipes(remaining)
    return "", 204


@app.get("/personal/all")
def all_personal_recipes():
    return jsonify(load_personal_recipes())


if __name__ == "__main__":
    app.run()
