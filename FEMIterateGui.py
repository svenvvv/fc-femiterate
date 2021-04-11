from FreeCAD import Units
from PySide import QtGui
from PySide.QtGui import QDialog, QMessageBox, QTableWidgetItem
from femmesh.gmshtools import GmshTools
from femtools import ccxtools
import FreeCAD,FreeCADGui,Part
import PySide
import json
import time

MSGBOX_TITLE = "Iterative CCX Solver"
FEMITERATE_VERSION = "0.0.1"

TYPEID_FEM_MESH = "Fem::FemMeshObjectPython"
TYPEID_FEM_ANALYSIS = "Fem::FemAnalysis"
TYPEID_FEM_SOLVER = "Fem::FemSolverObjectPython"

UI_BASE_PATH = FreeCAD.getUserMacroDir() + "FEMIterate"
UI_MAIN_FILE_PATH = f"{UI_BASE_PATH}/main.ui"
UI_CHANGE_FILE_PATH = f"{UI_BASE_PATH}/addchange.ui"
UI_CHECK_FILE_PATH = f"{UI_BASE_PATH}/addcheck.ui"

GUI_IN_SIDEBAR = True

USERTYPE_NUMBER = "Number"
USERTYPE_UNIT = "Unit"
USERTYPE_PYTHON = "Python expr."

BUILTIN_QUICK_EXPRESSIONS = [
    "max(r.vonMises) < 100",
    "max(r.Temperature) < 300"
]


def find_object_by_typeid(type, match_label=None):
    for obj in FreeCAD.ActiveDocument.Objects:
        if obj.TypeId == type:
            if match_label is None or obj.Label == match_label:
                return obj
    return None


def _validate_python_expr(expr):
    # TODO: Implement non-shit validation or remove completely
    print(f"Validating: {expr}")
    try:
        # NOTE: Not unused, eval variabless
        # x = 1.0
        # i = 10
        # i += eval(expr)
        return True
    except Exception as e:
        print(f"Validation error: {e}")
        return False


class AddCheckWindow():
    def __init__(self, settings, edit_expr=None):
        self.form = FreeCADGui.PySideUic.loadUi(UI_CHECK_FILE_PATH)
        self.expr = edit_expr
        self.settings = settings

        f = self.form

        if edit_expr:
            f.exprEdit.setText(edit_expr)

        f.addQuickExpr.clicked.connect(lambda: self._cb_mod_quickexpr(True))
        f.delQuickExpr.clicked.connect(lambda: self._cb_mod_quickexpr(False))

        f.buttonBox.accepted.connect(self._cb_accept)
        f.buttonBox.rejected.connect(self._cb_cancel)

        self._generate_quickexpr_table(settings.quick_expressions)

        set_quick_expr = lambda s: f.exprEdit.setText(f.quickExprList.item(s.row()).text())
        f.quickExprList.doubleClicked.connect(set_quick_expr)

    def _cb_mod_quickexpr(self, add=False):
        f = self.form
        qe = self.settings.quick_expressions
        if add:
            text = f.exprEdit.text()
            if len(text) > 0:
                qe.append(text)
            else:
                return
        else:
            row = f.quickExprList.currentRow()
            it = f.quickExprList.item(row)
            if it:
                qe.remove(it.text())
        self._generate_quickexpr_table(qe)

    def _generate_quickexpr_table(self, values):
        self.form.quickExprList.clear()
        for e in values:
            self.form.quickExprList.addItem(e)

    def _cb_accept(self):
        self.expr = self.form.exprEdit.text()

        if len(self.expr) == 0:
            QMessageBox.information(None, MSGBOX_TITLE, "Empty expression")
            return

        if _validate_python_expr(self.expr):
            self.form.accept()
        else:
            QMessageBox.information(None, MSGBOX_TITLE,
                    "Provided expression did not evaluate!\nCheck the report view for error log.")
            return

    def _cb_cancel(self):
        self.form.close()

class AddChangeWindow():
    def __init__(self, obj, selected_param=None, selected_value=None, selected_value_type=None):
        self.form = FreeCADGui.PySideUic.loadUi(UI_CHANGE_FILE_PATH)

        self.prop = None
        self.value = None
        self.type = None
        self.prop_type_lut = {}

        f = self.form
        f.buttonBox.accepted.connect(self._cb_accept)
        f.buttonBox.rejected.connect(self._cb_cancel)
        f.searchBox.textChanged.connect(self._search_fn)

        tbl = f.propsTable
        tbl.setRowCount(0)
        rowcount = 0
        for k in obj.PropertiesList:
            v = getattr(obj, k)

            # slice it so we don't just stuff large strings into the table
            contents = str(v)[:64]

            # TODO: Use prop types LUT for Python expr validation
            self.prop_type_lut[k] = type(v)

            tbl.insertRow(rowcount)
            tbl.setItem(rowcount, 0, QTableWidgetItem(k))
            tbl.setItem(rowcount, 1, QTableWidgetItem(contents))

            # Highlight selected value if we're in edit dialog
            if k == selected_param:
                tbl.selectRow(rowcount)

            rowcount += 1

        if selected_value:
            f.valueEdit.setText(selected_value)

    def _search_fn(self, text):
        match = self.form.propsTable.findItems(text, PySide.QtCore.Qt.MatchFlag.MatchStartsWith)
        match_rows = [m.row() for m in match]

        for row in range(self.form.propsTable.rowCount()):
            # Show matched rows or all rows if we cleared the textbox
            if row in match_rows or len(text) == 0:
                self.form.propsTable.showRow(row)
            else:
                self.form.propsTable.hideRow(row)

    def _cb_accept(self):
        val = self.form.valueEdit.text()

        selected_row = self.form.propsTable.currentRow()

        if not val:
            QMessageBox.information(None, MSGBOX_TITLE, "No value entered")
            return
        if selected_row < 0:
            QMessageBox.information(None, MSGBOX_TITLE, "No property selected")
            return

        self.value = val
        self.prop = self.form.propsTable.item(selected_row, 0).text()

        typ = self.form.typeBox.currentText()
        if typ == "Python expression":
            self.type = USERTYPE_PYTHON
            if not _validate_python_expr(val):
                QMessageBox.information(None, MSGBOX_TITLE,
                        "Expression evaluation failed.\nCheck the Python console for error log.")
                return
        elif typ == "Unit string":
            self.type = USERTYPE_UNIT
            # Attempt to parse unit so we can detect invalid units
            try:
                Units.Quantity(val)
            except ValueError:
                QMessageBox.information(None, MSGBOX_TITLE, "Invalid unit value")
                return
        elif typ == "Number":
            self.type = USERTYPE_NUMBER
        else:
            QMessageBox.information(None, MSGBOX_TITLE, "Invalid type")

        self.form.accept()

    def _cb_cancel(self):
        self.form.close()


class Settings():
    def __init__(self):
        self.changes = {}
        self.checks = []
        self.iteration_limit = 20
        self.csv_suffix = "femiterate.csv"
        self.quick_expressions = BUILTIN_QUICK_EXPRESSIONS
        self._obj = None

        self._obj = find_object_by_typeid("App::FeaturePython", "FEMIterateSettings")
        if not self._obj:
            self._obj = self.create_new()
        else:
            self.load()

    def create_new(self):
        obj = FreeCAD.ActiveDocument.addObject("App::FeaturePython", "FEMIterateSettings")
        # General configuration
        obj.addProperty("App::PropertyInteger", "IterationLimit", "Configuration", "")
        obj.addProperty("App::PropertyString", "CsvSuffix", "Configuration", "")
        # TODO: probably should check if we're missing some settings on load if we're a newer ver
        obj.addProperty("App::PropertyString", "FEMIterateVersion", "Configuration", "")
        # Quick expressions
        obj.addProperty("App::PropertyString", "QuickExpressions", "Configuration", "")
        # Parameters
        obj.addProperty("App::PropertyString", "Changes", "Parameters", "")
        obj.addProperty("App::PropertyString", "Checks", "Parameters", "")

        self._obj = obj
        self.save()
        return obj

    def load(self):
        self.iteration_limit = self._obj.IterationLimit
        self.csv_suffix = self._obj.CsvSuffix

        # No need to load FEMIterateVersion
        self.quick_expressions = json.loads(self._obj.QuickExpressions)

        # TODO: check if we still actually have these objects and
        # fill the "orig" field with latest data.
        self.changes = json.loads(self._obj.Changes)
        self.checks = json.loads(self._obj.Checks)

    def save(self):
        self._obj.IterationLimit = self.iteration_limit
        self._obj.CsvSuffix = self.csv_suffix

        self._obj.FEMIterateVersion = FEMITERATE_VERSION
        self._obj.QuickExpressions = json.dumps(self.quick_expressions)

        self._obj.Changes = json.dumps(self.changes)
        self._obj.Checks = json.dumps(self.checks)


class MainWindow():
    def __init__(self, doc):
        self.form = FreeCADGui.PySideUic.loadUi(UI_MAIN_FILE_PATH)
        self._calculation_running = False

        self._settings = Settings()
        self._apply_settings()

        self._fem_mesh = None
        self._fem_analysis = None
        self._fem_solver = None

        f = self.form

        f.progressBar.setVisible(False)
        f.progressText.setVisible(False)

        f.objectsAutoButton.clicked.connect(lambda:
                self._find_mesh_and_analysis_objects(True))
        f.analysisSelect.clicked.connect(lambda:
                self._set_objects_tab_val("_fem_analysis", self._select_object(),
                    f.analysisEdit, TYPEID_FEM_ANALYSIS))
        f.meshSelect.clicked.connect(lambda:
                self._set_objects_tab_val("_fem_mesh", self._select_object(),
                    f.meshEdit, TYPEID_FEM_MESH))
        f.solverSelect.clicked.connect(lambda:
                self._set_objects_tab_val("_fem_solver", self._select_object(),
                    f.solverEdit, TYPEID_FEM_SOLVER))

        f.calculateButton.clicked.connect(self._calculate)

        # Options
        f.iterationLimitEdit.valueChanged.connect(lambda v:
                self._cb_iteration_limit_changed(v))

        # NOTE: tables modify_X are called from a lambda because we don't
        # want Qt to mangle the first argument (which is None to indicate
        # adding of new object to table).

        # Changes table buttons
        f.changesAdd.clicked.connect(lambda: self._modify_changes())
        f.changesRemove.clicked.connect(self._cb_remove_change)

        changes_edit_fn = lambda: self._modify_changes(f.changesView.currentRow())
        f.changesEdit.clicked.connect(changes_edit_fn)
        f.changesView.doubleClicked.connect(changes_edit_fn)

        # Checks table buttons
        f.checksAdd.clicked.connect(lambda: self._modify_checks())
        f.checksRemove.clicked.connect(self._cb_remove_check)

        checks_edit_fn = lambda: self._modify_checks(f.checksView.currentRow())
        f.checksEdit.clicked.connect(checks_edit_fn)
        f.checksView.doubleClicked.connect(checks_edit_fn)

        if not self._find_mesh_and_analysis_objects(False):
            f.tabWidget.setCurrentIndex(0)

    def _cb_remove_change(self):
        self.form.changesView.removeRow(self.form.changesView.currentRow())
        # TODO: we don't have to re-read the whole table every time
        self._settings.changes = self._read_changes_from_table()

    def _cb_remove_check(self):
        sel = self.form.checksView.selectedIndexes()
        if len(sel) > 0:
            self.form.checksView.takeItem(sel[0].row())
            # TODO: we don't have to re-read the whole table every time
            self._settings.checks = self._read_checks_from_table()

    def _cb_iteration_limit_changed(self, val):
        self._settings.iteration_limit = val

    def _apply_settings(self):
        f = self.form
        s = self._settings

        f.iterationLimitEdit.setValue(s.iteration_limit)
        f.csvFilenameEdit.setText(s.csv_suffix)

        # Apply changes to table
        if s.changes is not None:
            for (objname, changes) in s.changes.items():
                print(changes)
                for (prop, change) in changes.items():
                    self._add_or_modify_change(objname, prop,
                            change["val"], change["type"])

        if s.changes is not None:
            for expr in s.checks:
                f.checksView.addItem(expr)


    def _read_changes_from_table(self):
        ret = {}
        changes_view = self.form.changesView

        for row_idx in range(changes_view.rowCount()):
            val_item = changes_view.item(row_idx, 2)
            objname = changes_view.item(row_idx, 0).text()
            objref = FreeCAD.ActiveDocument.getObject(objname)
            prop = changes_view.item(row_idx, 1).text()

            if objname not in ret:
                ret[objname] = {}

            ret[objname][prop] = {
                "val": val_item.text(),
                "type": val_item.toolTip(),
                "orig": str(getattr(objref, prop))
            }
        return ret

    def _read_checks_from_table(self):
        ret = []
        checks_view = self.form.checksView

        for row_idx in range(checks_view.count()):
            expr = checks_view.item(row_idx)
            ret.append(expr.text())

        return ret

    @staticmethod
    def _apply_delta_changes(changes_dict):
        for (objname, changes) in changes_dict.items():
            obj = FreeCAD.ActiveDocument.getObject(objname)
            for (prop, change) in changes.items():
                prev = getattr(obj, prop)

                if change["type"] == USERTYPE_UNIT:
                    new_val = prev + Units.Quantity(change["val"])
                    setattr(obj, prop, new_val)
                else:
                    print(f"No support for user type {change['type']} yet...")

    @staticmethod
    def _revert_delta_changes(changes_dict):
        for (objname, changes) in changes_dict.items():
            obj = FreeCAD.ActiveDocument.getObject(objname)
            for (prop, change) in changes.items():
                setattr(obj, prop, change["orig"])

    def _find_rename_latest_result(self, cur_iteration):
        # TODO HACK: we assume that the latest FEM result obj is our result, there probably
        # is a better way. This could cause some bugs later on.
        for femobj in self._fem_analysis.Group:
            if femobj.isDerivedFrom("Fem::FemResultObject") and not femobj.Label.startswith("Iteration"):
                femobj.Label = f"Iteration{cur_iteration}"
                return femobj
        return None

    @staticmethod
    def _eval_checks(checks, result, iteration):
        for expr in checks:
            # NOTE: not unused, these are for access from user expression
            i = iteration
            r = result
            ret = eval(expr)

            if ret is not True:
                return False
        return True

    @staticmethod
    def _calculate_single_shot(fea, changes, checks, iteration, logBox, fem_mesh):
        """
        @returns Tri-state logic.
                 None indicates loop-again, True indicates that we found the
                 correct solution and False indicates that something went wrong
        """
        logBox.append("Applying changes...")
        MainWindow._apply_delta_changes(changes)

        doc = FreeCAD.ActiveDocument

        logBox.append("Meshing...")
        doc.recompute()
        fem_mesh.create_mesh()

        logBox.append("Solving...")
        fea.update_objects()
        fea.setup_working_dir()
        fea.setup_ccx()
        fem_msg = fea.check_prerequisites()

        if not fem_msg:
            fea.write_inp_file()
            fea.ccx_run()
            fea.load_results()
        else:
            logBox.append(f"CCX setup error: {fem_msg}")
            return False

        return None

    def _calculate(self):
        self._settings.save()

        print("Starting solving...")

        if not self._fem_mesh:
            self.form.logBox.append(f"<b>Missing FEM mesh, see options tab</b>")
            return
        if not self._fem_solver:
            self.form.logBox.append(f"<b>Missing FEM solver, see options tab</b>")
            return
        if not self._fem_analysis:
            self.form.logBox.append(f"<b>Missing FEM analysis, see options tab</b>")
            return

        self._calculation_running = True

        max_iterations = self._settings.iteration_limit
        print(f"Max iterations: {max_iterations}")

        start_time = time.time()

        # Show Log tab and progress bar
        self.form.tabWidget.setCurrentIndex(2)
        self.form.progressBar.setVisible(True)
        self.form.progressText.setVisible(True)

        # Aggregate all checks and changes from table cells

        # Loop until we hit max iterations or a check passes
        condition_fail = False
        iteration = 0

        changes = self._settings.changes
        checks = self._settings.checks

        try:
            # NOTE: we pick out the analysis and solver ourselves because for
            # some reason ccxtools did not find it at times. No idea why.
            fea = ccxtools.FemToolsCcx(analysis=self._fem_analysis, solver=self._fem_solver)
            fea.purge_results()
        # ccxtools throws raw Exceptions, not a lazy catch-all here
        except Exception as e:
            self.form.logBox.append(f"<b>{e}</b>")
            condition_fail = True

        while not condition_fail and iteration < max_iterations:
            self.form.progressBar.setValue(float(iteration+1) / float(max_iterations) * 100)
            self.form.progressText.setText(f"Running iteration {iteration+1}/{max_iterations}")
            self.form.logBox.append(f"<b>Running iteration {iteration+1}</b>")

            ret = self._calculate_single_shot(fea, changes, checks,
                                              iteration, self.form.logBox, self._fem_mesh)
            if ret is not None:
                if ret is False:
                    condition_fail = True
                break

            self.form.logBox.append("Checking...")
            current_result = self._find_rename_latest_result(iteration)

            if self._eval_checks(checks, current_result, iteration):
                self.form.logBox.append("All checks passed!")
                break

            iteration += 1

        if iteration == max_iterations:
            self.form.logBox.append(f"<b>Hit iteration limit without finding a solution</b>")

        if condition_fail:
            # Hide progressbars on failure since they can be in a weird state
            self.form.progressBar.setVisible(False)
            self.form.progressText.setVisible(False)
            self.form.logBox.append(f"<b>Had an error!</b>")

        self.form.logBox.append("Restoring original values...")
        self._revert_delta_changes(changes)

        self.form.logBox.append("<b>Done!</b>")

        elapsed_time = round(time.time() - start_time, 1)
        print(f"Solving finished in {elapsed_time} s")

        self.form.progressText.setText(f"Finished in {elapsed_time} s, computed {iteration+1} iterations")
        self.form.progressBar.setVisible(False)
        self._calculation_running = False

    def _modify_checks(self, modify_row_idx=None):
        # Early out if the user clicked edit without selecting a row
        if modify_row_idx == -1:
            return

        edit_mode = False
        expr = None
        widget = self.form.checksView

        if modify_row_idx is not None and modify_row_idx >= 0:
            expr = widget.item(modify_row_idx).text()
            edit_mode = True

        f = AddCheckWindow(self._settings, expr)

        if f.form.exec_():
            if edit_mode:
                widget.item(modify_row_idx).setText(f.expr)
            else:
                widget.addItem(f.expr)

        # TODO: we don't have to re-read the whole table every time
        self._settings.checks = self._read_checks_from_table()

    def _add_or_modify_change(self, objname, prop, value, type, row_number=None):
        table = self.form.changesView
        if not row_number:
            row_count = table.rowCount()
            row_number = row_count

            table.insertRow(row_number)
            table.setItem(row_number, 0, QTableWidgetItem())
            table.setItem(row_number, 1, QTableWidgetItem())
            table.setItem(row_number, 2, QTableWidgetItem())

        table.item(row_number, 0).setText(objname)
        table.item(row_number, 1).setText(prop)
        val_item = table.item(row_number, 2)
        # A lil' HACK. Store the value type as tooltip so we can retrieve it later :)
        val_item.setText(value)
        val_item.setToolTip(type)


    def _modify_changes(self, modify_row_idx=None):
        # Early out if the user clicked edit without selecting a row
        if modify_row_idx == -1:
            return

        table = self.form.changesView
        obj = self._select_object()
        if obj:
            sel_prop = None
            sel_val = None

            if modify_row_idx is not None and modify_row_idx >= 0:
                # sel_obj = table.item(modify_row_idx, 0).text()
                sel_prop = table.item(modify_row_idx, 1).text()
                sel_val = table.item(modify_row_idx, 2).text()
            else:
                modify_row_idx = None

            f = AddChangeWindow(obj, sel_prop, sel_val)

            if f.form.exec_():
                self._add_or_modify_change(obj.Label, f.prop, f.value, f.type, modify_row_idx)
                # TODO: we don't have to re-read the whole table every time
                self._settings.changes = self._read_changes_from_table()

    def _select_object(self):
        selection = FreeCADGui.Selection.getSelection()
        if selection and len(selection) == 1:
            return selection[0]
        else:
            QMessageBox.information(None, MSGBOX_TITLE, "Select a single object")
            return None

    def _set_objects_tab_val(self, dest, obj, textedit, expected_type=None, provided_label=None):
        # NOTE: check for obj because we're calling this from button callbacks
        if obj and (not expected_type or obj.TypeId == expected_type):
            if provided_label:
                textedit.setText(provided_label)
            else:
                textedit.setText(obj.Label)
            print(f"Set {dest} to {obj}")
            setattr(self, dest, obj)
        else:
            QMessageBox.information(None, MSGBOX_TITLE, f"Selected object is not a {expected_type}")

    def _find_mesh_and_analysis_objects(self, complain=True):
        found_something = False
        f = self.form
        mesh = find_object_by_typeid(TYPEID_FEM_MESH)
        an = find_object_by_typeid(TYPEID_FEM_ANALYSIS)
        solver = find_object_by_typeid(TYPEID_FEM_SOLVER)

        if mesh:
            found_something = True
            self._set_objects_tab_val("_fem_mesh", GmshTools(mesh), f.meshEdit, None, mesh.Label)
            print(f"Found mesh {mesh.Label}")
        if an:
            found_something = True
            self._set_objects_tab_val("_fem_analysis", an, f.analysisEdit)
            print(f"Found analysis {an.Label}")
        if solver:
            found_something = True
            self._set_objects_tab_val("_fem_solver", solver, f.solverEdit)
            print(f"Found solver {solver.Label}")

        if not found_something:
            print(f"Did not autofind any objects!!")
            if complain:
                QMessageBox.information(None, MSGBOX_TITLE, "Did not find any objects")

        return mesh and an

    def show_window(self):
        self.form.exec_()

    def accept(self):
        self._settings.save()
        if GUI_IN_SIDEBAR:
            FreeCADGui.Control.closeDialog()

doc = FreeCAD.ActiveDocument

if doc is None:
    QMessageBox.information(None, MSGBOX_TITLE, "No active document")
else:
    gui = MainWindow(doc)

    if GUI_IN_SIDEBAR:
        FreeCADGui.Control.showDialog(gui)
    else:
        gui.show_window()

