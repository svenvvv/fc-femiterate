from FreeCAD import Units
from PySide.QtGui import QDialog, QMessageBox, QTableWidgetItem
from femmesh.gmshtools import GmshTools
from femtools import ccxtools
import FreeCAD,FreeCADGui,Part
import PySide
import time

MSGBOX_TITLE = "Iterative CCX Solver"

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


def find_object_by_typeid(document, type):
    for obj in document.Objects:
        if obj.TypeId == type:
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
    def __init__(self, edit_expr=None):
        self.form = FreeCADGui.PySideUic.loadUi(UI_CHECK_FILE_PATH)
        self.expr = edit_expr

        f = self.form

        if edit_expr:
            f.exprEdit.setText(edit_expr)

        f.buttonBox.accepted.connect(self._cb_accept)
        f.buttonBox.rejected.connect(self._cb_cancel)

        for e in BUILTIN_QUICK_EXPRESSIONS:
            f.quickExprList.addItem(e)

        set_quick_expr = lambda s: f.exprEdit.setText(f.quickExprList.item(s.row()).text())
        f.quickExprList.doubleClicked.connect(set_quick_expr)

    def _cb_accept(self):
        self.expr = self.form.exprEdit.text()

        if _validate_python_expr(self.expr):
            self.form.accept()
        else:
            QMessageBox.information(None, MSGBOX_TITLE,
                    "Provided expression did not evaluate!\nCheck the report view for error log.")

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
                        'Expression evaluation failed.\nCheck the Python console for error log.')
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


class MainWindow():
    def __init__(self, doc):
        self._document = doc
        self.form = FreeCADGui.PySideUic.loadUi(UI_MAIN_FILE_PATH)

        f = self.form

        f.progressBar.setVisible(False)
        f.progressText.setVisible(False)

        f.objectsAutoButton.clicked.connect(lambda: self._find_mesh_and_analysis_objects(False))
        f.analysisSelect.clicked.connect(self._select_fem_analysis)
        f.meshSelect.clicked.connect(self._select_fem_mesh)

        f.calculateButton.clicked.connect(self._calculate)

        # Changes table buttons
        f.changesAdd.clicked.connect(lambda: self._modify_changes())

        f.changesRemove.clicked.connect(lambda: f.changesView.removeRow(f.changesView.currentRow()))

        changes_edit_fn = lambda: self._modify_changes(f.changesView.currentRow())
        f.changesEdit.clicked.connect(changes_edit_fn)
        f.changesView.doubleClicked.connect(changes_edit_fn)

        # Checks table buttons
        f.checksAdd.clicked.connect(lambda: self._modify_checks())
        f.checksRemove.clicked.connect(lambda: f.checksView.takeItem(f.checksView.selectedIndexes()[0].row()))

        checks_edit_fn = lambda: self._modify_checks(f.checksView.currentRow())
        f.checksEdit.clicked.connect(checks_edit_fn)
        f.checksView.doubleClicked.connect(checks_edit_fn)

        if not self._find_mesh_and_analysis_objects(False):
            f.tabWidget.setCurrentIndex(0)

    def _calculate(self):
        print("Starting solving...")

        max_iterations = int(self.form.iterationLimitEdit.text())
        print(f"Max iterations: {max_iterations}")

        start_time = time.time()

        # Show Log tab and progress bar
        self.form.tabWidget.setCurrentIndex(2)
        self.form.progressBar.setVisible(True)
        self.form.progressText.setVisible(True)

        change_view = self.form.changesView
        check_view = self.form.checksView

        # Aggregate all checks and changes from table cells
        original_values = {}
        changes_dict = {}
        checks_list = []

        for row_idx in range(change_view.rowCount()):
            val_item = change_view.item(row_idx, 2)
            objname = change_view.item(row_idx, 0).text()
            objref = self._document.getObject(objname)
            prop = change_view.item(row_idx, 1).text()

            if objname not in changes_dict:
                changes_dict[objname] = {}

            changes_dict[objname][prop] = {
                "val": val_item.text(),
                "typ": val_item.toolTip()
            }

            # Store original value so we can restore it later
            if objname not in original_values:
                original_values[objname] = {}
            original_values[objname][prop] = getattr(objref, prop)

        for row_idx in range(check_view.count()):
            expr = check_view.item(row_idx)
            checks_list.append(expr.text())

        # Loop until we hit max iterations or a check passes
        condition_fail = False
        iteration = 0

        try:
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

            self.form.logBox.append("Applying changes...")
            for (objname, changes) in changes_dict.items():
                obj = self._document.getObject(objname)
                for (prop, change) in changes.items():
                    prev = getattr(obj, prop)

                    if change['typ'] == USERTYPE_UNIT:
                        new_val = prev + Units.Quantity(change['val'])
                        setattr(obj, prop, new_val)
                    else:
                        self.form.logBox.append(f"No support for user type {change['typ']} yet...")

            self.form.logBox.append("Meshing...")
            self._document.recompute()
            self._fem_mesh.create_mesh()

            self.form.logBox.append("Solving...")
            fea.update_objects()
            fea.setup_working_dir()
            fea.setup_ccx()
            fem_msg = fea.check_prerequisites()

            if not fem_msg:
                fea.write_inp_file()
                fea.ccx_run()
                fea.load_results()
            else:
                self.form.logBox.append(f"CCX setup error: {fem_msg}")
                condition_fail = True
                break

            self.form.logBox.append("Checking...")

            current_result = None
            # TODO HACK: we assume that the latest FEM result obj is our result, there probably
            # is a better way. This could cause some bugs later on.
            for femobj in self._fem_analysis.Group:
                if femobj.isDerivedFrom("Fem::FemResultObject") and not femobj.Label.startswith("Iteration"):
                    femobj.Label = f"Iteration{iteration}"
                    current_result = femobj
                    break

            print(f"Last FEM result: {current_result.Label}")

            passed_checks = 0
            for expr in checks_list:
                # NOTE: not unused, these are for access from user expression
                x = getattr(obj, prop)
                i = iteration
                r = current_result
                ret = eval(expr)

                if ret is True:
                    passed_checks += 1

            if passed_checks == len(checks_list):
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

        for (objname, changes) in original_values.items():
            obj = self._document.getObject(objname)
            for (prop, val) in changes.items():
                setattr(obj, prop, val)

        self.form.logBox.append("Done!")

        elapsed_time = round(time.time() - start_time, 1)
        print(f"Solving finished in {elapsed_time} s")

        self.form.progressText.setText(f"Finished in {elapsed_time} s, computed {iteration} iterations")
        self.form.progressBar.setVisible(False)


    def _clear_object_table(self, table):
        table.setRowCount(0)

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

        f = AddCheckWindow(expr)

        if f.form.exec_():
            if edit_mode:
                widget.item(modify_row_idx).setText(f.expr)
            else:
                widget.addItem(f.expr)


    def _modify_changes(self, modify_row_idx=None):
        # Early out if the user clicked edit without selecting a row
        if modify_row_idx == -1:
            return

        table = self.form.changesView
        obj = self._select_object()
        if obj:
            edit_mode = False
            sel_prop = None
            sel_val = None

            if modify_row_idx is not None and modify_row_idx >= 0:
                # sel_obj = table.item(modify_row_idx, 0).text()
                sel_prop = table.item(modify_row_idx, 1).text()
                sel_val = table.item(modify_row_idx, 2).text()
                edit_mode = True

            f = AddChangeWindow(obj, sel_prop, sel_val)

            if f.form.exec_():
                if not edit_mode:
                    rowcount = table.rowCount()
                    modify_row_idx = rowcount

                    table.insertRow(rowcount)
                    table.setItem(rowcount, 0, QTableWidgetItem())
                    table.setItem(rowcount, 1, QTableWidgetItem())
                    table.setItem(rowcount, 2, QTableWidgetItem())

                table.item(modify_row_idx, 0).setText(obj.Name)
                table.item(modify_row_idx, 1).setText(f.prop)
                val_item = table.item(modify_row_idx, 2)
                # A lil' HACK. Store the value type as tooltip so we can retrieve it later :)
                val_item.setText(str(f.value))
                val_item.setToolTip(f.type)

                return

    def _select_object(self):
        selection = FreeCADGui.Selection.getSelection()
        if selection and len(selection) == 1:
            return selection[0]
        else:
            QMessageBox.information(None, MSGBOX_TITLE, "Select a single object")
            return None

    def _set_fem_mesh(self, mesh):
        if mesh and mesh.TypeId == TYPEID_FEM_MESH:
            self._fem_mesh = GmshTools(mesh)
            self.form.meshEdit.setText(mesh.Label)
        else:
            QMessageBox.information(None, MSGBOX_TITLE, "Selected object is not a FEM mesh")

    def _set_fem_analysis(self, analysis):
        if analysis and analysis.TypeId == TYPEID_FEM_ANALYSIS:
            self._fem_analysis = analysis
            self.form.analysisEdit.setText(analysis.Label)
        else:
            QMessageBox.information(None, MSGBOX_TITLE, "Selected object is not a FEM analysis")

    def _find_mesh_and_analysis_objects(self, complain=True):
        found_something = False
        mesh = find_object_by_typeid(self._document, TYPEID_FEM_MESH)
        an = find_object_by_typeid(self._document, TYPEID_FEM_ANALYSIS)
        solver = find_object_by_typeid(self._document, TYPEID_FEM_SOLVER)

        if mesh:
            found_something = True
            self._set_fem_mesh(mesh)
            print(f'Found mesh {mesh.Label}')
        if an:
            found_something = True
            self._set_fem_analysis(an)
            print(f'Found analysis {an.Label}')
        if solver:
            found_something = True
            self._fem_solver = solver
            print(f'Found solver {solver.Label}')

        if not found_something and complain:
            QMessageBox.information(None, MSGBOX_TITLE, "Did not find any objects")

        return mesh and an

    def _select_fem_mesh(self):
        obj = self._select_object()
        if obj:
            self._set_fem_mesh(obj)

    def _select_fem_analysis(self):
        obj = self._select_object()
        if obj:
            self._set_fem_analysis(obj)

    def show_window(self):
        self.form.exec_()

    def accept(self):
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

