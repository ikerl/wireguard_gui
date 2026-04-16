from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from .database import User, db

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login."""
    return User.query.get(int(user_id))


def init_login_manager(app):
    """Initialize login manager with Flask app."""
    login_manager.init_app(app)


def create_admin_user(username, password):
    """Create the first admin user."""
    if User.query.first():
        return False, "Ya existe un usuario administrador"

    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return True, "Usuario administrador creado correctamente"


def create_user(username, password):
    """Create a new user."""
    if User.query.filter_by(username=username).first():
        return False, "El nombre de usuario ya existe"

    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return True, "Usuario creado correctamente"


def delete_user(user_id):
    """Delete a user by ID."""
    user = User.query.get(user_id)
    if not user:
        return False, "Usuario no encontrado"

    if user.id == current_user.id:
        return False, "No puedes eliminarte a ti mismo"

    db.session.delete(user)
    db.session.commit()
    return True, "Usuario eliminado correctamente"


def get_all_users():
    """Get all users ordered by creation date."""
    return User.query.order_by(User.created_at.desc()).all()
